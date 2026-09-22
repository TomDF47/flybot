from __future__ import annotations

import asyncio
import base64
import binascii
from dataclasses import dataclass
from os import getenv
from pathlib import Path
from time import monotonic, time_ns
from typing import Any

from flybot_body_flygym import create_body_adapter
from flybot_brain import create_cognitive_provider
from flybot_brain.jev import (
    JEV_FAILURE_COOLDOWN_S,
    JEV_RUNNING_STATUS,
    BoundedActionOption,
    JevClient,
    JevSelectionFailed,
    action_key,
    active_cooldown_keys,
    build_action_options,
    choose_bounded_action,
    compact_observation,
    create_jev_client,
)
from flybot_core import FlyBotConfig, load_config
from flybot_core.config import EnvironmentSettings
from flybot_core.ids import new_identifier
from flybot_core.models import Frame, MotorCommand, OnFailurePolicy, PlanStep, Pose2D, WorldState
from flybot_executive import MissionExecutive
from flybot_flycore import L0FlyCoreController
from flybot_perception import ObjectTrackResolver
from flybot_safety import SafetyKernel
from flybot_telemetry import TelemetryRecorder

_JEV_POLL_INTERVAL_S = 0.05


@dataclass
class RuntimeStatus:
    frame: Frame
    safety_flags: list[str]
    mission_state: dict[str, Any]
    world_state: WorldState
    body_state: dict[str, Any]
    controller_state: dict[str, Any]
    backend: str


class FlyBotRuntime:
    def __init__(self, config: FlyBotConfig | None = None) -> None:
        self.config = config or load_config()
        self.environment_settings = EnvironmentSettings()
        self.body_adapter = create_body_adapter(self.config)
        self.flycore = L0FlyCoreController()
        self.resolver = ObjectTrackResolver(
            use_ground_truth_for_tests=self.config.simulation.allow_ground_truth_for_tests
        )
        self.executive = MissionExecutive(config=self.config, resolver=self.resolver)
        self.safety_kernel = SafetyKernel(self.config.safety)
        telemetry_output = getenv("TELEMETRY_OUTPUT_JSONL", "").strip()
        self._session_recording_enabled = self.environment_settings.session_recording
        self._recordings_root = Path(self.environment_settings.recordings_root)
        self._recording_frame_interval_ns = max(
            100_000_000, int(self.environment_settings.recording_frame_interval_s * 1_000_000_000)
        )
        self._last_recorded_frame_ns: int = 0
        self._recording_frame_count = 0
        self._recording_session_id = f"{new_identifier('session')}_{time_ns()}"
        self._recording_output_dir: Path | None = None
        self._recording_frames_dir: Path | None = None
        telemetry_path: Path | None = Path(telemetry_output) if telemetry_output else None
        if telemetry_path is None and self._session_recording_enabled:
            recording_output_dir = self._ensure_recording_output_dir()
            telemetry_path = recording_output_dir / "telemetry.jsonl"
        self.recorder = TelemetryRecorder(
            output_path=telemetry_path
        )
        self.cognitive_provider = create_cognitive_provider(self.config.brain)
        self._jev_client: JevClient = create_jev_client(
            model=self.config.brain.jev_model,
            timeout_s=self.config.brain.jev_request_timeout_s,
            openrouter_api_key=self.environment_settings.openrouter_api_key,
            typesafe_api_key=self.environment_settings.typesafe_api_key,
        )
        self.latest_body_state: dict[str, Any] = {}
        self.latest_frame = Frame(timestamp_ns=0, is_placeholder=True, data_url=None)
        self.latest_world_state = WorldState(
            robot_pose=Pose2D(x=0.0, y=0.0, heading_rad=0.0),
            home_pose=Pose2D(x=0.0, y=0.0, heading_rad=0.0),
            objects=[],
        )
        self.latest_safety_flags: list[str] = []
        self._active_intent_id: str | None = None
        self._control_task: asyncio.Task[None] | None = None
        self._jev_task: asyncio.Task[None] | None = None
        self._running = False
        self._recording_override: bool = False
        self._planner_script_steps: list[PlanStep] = []
        self._planner_ready = False
        self._released_step_ids: set[str] = set()
        self._decision_count = 0
        self._consecutive_decision_failures = 0
        self._recent_action_results: list[dict[str, object]] = []
        self._failure_cooldowns: dict[str, float] = {}
        self._completed_steps_seen = 0
        self._pending_action_labels: dict[str, str] = {}
        self._loop_phase = "idle"
        self._loop_status_text = "idle"
        self._loop_objective: str | None = None
        self._loop_instruction: str | None = None
        self._selected_action_description: str | None = None
        self._decision_generation = 0

    async def start(self) -> None:
        await self.reset(seed=self.config.simulation.seed)
        self._running = True
        self._control_task = asyncio.create_task(self._control_loop(), name="flybot-control-loop")
        if self.config.brain.jev_enabled:
            self._jev_task = asyncio.create_task(self._jev_decision_loop(), name="flybot-jev-loop")

    async def stop(self) -> None:
        self._running = False
        await self._cancel_task(self._control_task)
        await self._cancel_task(self._jev_task)
        self._control_task = None
        self._jev_task = None

    async def e_stop(self) -> None:
        self._decision_generation += 1
        self.safety_kernel.set_estop_latched(True)
        await self.body_adapter.safe_stop()
        self.recorder.emit("SAFETY_STOP", {"source": "api_estop"})
        self._finish_decision_loop("estop")

    async def reset(self, seed: int) -> None:
        self._decision_generation += 1
        if self._loop_phase == "running":
            self.recorder.emit(
                "JEV_LOOP_DONE",
                {
                    "reason": "reset",
                    "decision_count": self._decision_count,
                    "model": self._jev_client.model,
                    "source": self._jev_client.source,
                },
            )
        self._clear_decision_loop_state()
        self.safety_kernel.reset()
        self.flycore.reset()
        body_state = await self.body_adapter.reset(seed=seed)
        self.latest_body_state = body_state.model_dump(mode="json")
        world_snapshot = self.body_adapter.world_snapshot()
        self.latest_world_state = self.resolver.resolve_world_state(
            robot_pose=body_state.pose,
            world_snapshot=world_snapshot,
        )
        self.latest_frame = self.body_adapter.low_rate_frame()
        self.latest_safety_flags = []
        self._active_intent_id = None
        self._recording_override = False
        self._last_recorded_frame_ns = 0
        self.recorder.emit("SIM_RESET", {"seed": seed})

    async def submit_instruction(self, instruction: str) -> str:
        plan = await self.cognitive_provider.build_plan(instruction, self.latest_world_state)
        if not self.config.brain.jev_enabled:
            self.executive.set_plan(plan)
            self.recorder.emit(
                "PLAN_ACCEPTED",
                {"mission_id": plan.mission_id, "step_count": len(plan.steps), "jev_loop": False},
            )
            return plan.mission_id

        self._decision_generation += 1
        self._planner_script_steps = list(plan.steps)
        self._planner_ready = True
        self._released_step_ids = set()
        self._decision_count = 0
        self._consecutive_decision_failures = 0
        self._recent_action_results = []
        self._failure_cooldowns = {}
        self._completed_steps_seen = 0
        self._pending_action_labels = {}
        self._selected_action_description = None
        self._loop_objective = plan.objective
        self._loop_instruction = instruction
        self._loop_phase = "running"
        self._loop_status_text = JEV_RUNNING_STATUS
        self.executive.begin_decision_loop(
            mission_id=plan.mission_id,
            objective=plan.objective,
            assumptions=plan.assumptions,
            completion_summary_fields=plan.completion_summary_fields,
        )
        self.recorder.emit(
            "PLAN_ACCEPTED",
            {
                "mission_id": plan.mission_id,
                "step_count": len(plan.steps),
                "jev_loop": True,
            },
        )
        self.recorder.emit(
            "JEV_LOOP_STARTED",
            {
                "mission_id": plan.mission_id,
                "model": self._jev_client.model,
                "source": self._jev_client.source,
                "status": JEV_RUNNING_STATUS,
            },
        )
        return plan.mission_id

    def status(self, *, include_frame_data_url: bool = True) -> RuntimeStatus:
        mission_state = self.executive.current_state()
        recording_enabled = self._is_recording_enabled()
        plan_step_count = 0 if mission_state.plan is None else len(mission_state.plan.steps)
        frame = self.latest_frame
        if not include_frame_data_url and frame.data_url is not None:
            frame = frame.model_copy(update={"data_url": None})
        return RuntimeStatus(
            frame=frame,
            safety_flags=self.latest_safety_flags,
            mission_state={
                "mission_id": mission_state.mission_id,
                "active_step_index": mission_state.active_step_index,
                "completed_steps": [
                    result.model_dump(mode="json") for result in mission_state.completed_steps
                ],
                "failed_step": None
                if mission_state.failed_step is None
                else mission_state.failed_step.model_dump(mode="json"),
                "timeline": mission_state.timeline,
                "recording_enabled": recording_enabled,
                "change_events_count": mission_state.change_events_count,
                "follow_min_distance_observed": mission_state.follow_min_distance_observed,
                "touch_contact_count": mission_state.touch_contact_count,
                "touch_target_object_id": mission_state.touch_target_object_id,
                "recording_output_dir": None
                if self._recording_output_dir is None
                else str(self._recording_output_dir),
                "recording_frame_count": self._recording_frame_count,
                "plan_step_count": plan_step_count,
                "awaiting_decision": mission_state.awaiting_decision,
                "decision_loop": self._decision_loop_status(),
            },
            world_state=self.latest_world_state,
            body_state=self.latest_body_state,
            controller_state=self.flycore.status().model_dump(mode="json"),
            backend=self.config.simulation.backend,
        )

    async def _jev_decision_loop(self) -> None:
        while self._running:
            try:
                await self._jev_iteration()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.recorder.emit(
                    "JEV_LOOP_ERROR",
                    {"error_type": type(error).__name__, "detail": str(error)[:200]},
                )
                self._finish_decision_loop("failed")
            await asyncio.sleep(_JEV_POLL_INTERVAL_S)

    async def _jev_iteration(self) -> None:
        if not self.config.brain.jev_enabled or self._loop_phase != "running":
            return
        if self.safety_kernel.estop_latched:
            self._finish_decision_loop("estop")
            return
        if not self._planner_ready:
            return
        self._sync_completed_action_results()
        if self.executive.mission_loop_finished():
            self._finish_decision_loop("completed")
            return
        mission_state = self.executive.current_state()
        if mission_state.failed_step is not None:
            self._handle_decision_failure()
            return
        if not self.executive.needs_decision():
            return
        if self._decision_count >= self.config.brain.jev_max_decisions:
            self._finish_decision_loop("budget")
            return
        await self._decide_and_enqueue()

    async def _decide_and_enqueue(self) -> None:
        decision_generation = self._decision_generation

        def observe(kind: str, detail: dict[str, object]) -> None:
            event_name = "JEV_DECISION_STALE" if kind == "stale" else "JEV_DECISION_FAILED"
            self.recorder.emit(event_name, {"kind": kind, **detail})

        try:
            selected_option, choice = await choose_bounded_action(
                client=self._jev_client,
                options_for_attempt=self._current_action_options,
                observation_for_attempt=self._current_observation,
                pose_reader=lambda: self.latest_world_state.robot_pose,
                stale_response_s=self.config.brain.jev_stale_response_s,
                stale_displacement=self.config.brain.jev_stale_displacement,
                observe=observe,
            )
        except JevSelectionFailed as error:
            if decision_generation != self._decision_generation:
                return
            self.recorder.emit(
                "JEV_DECISION_FAILED",
                {"error_type": error.reason, "kind": "selection_failed"},
            )
            self._finish_decision_loop("failed")
            return
        if decision_generation != self._decision_generation or self.safety_kernel.estop_latched:
            self.recorder.emit("JEV_DECISION_STALE", {"reason": "cancelled", "kind": "stale"})
            return
        selected_step = selected_option.step
        self._released_step_ids.add(selected_step.step_id)
        self._decision_count += 1
        self._selected_action_description = selected_option.description
        self._pending_action_labels[selected_step.step_id] = selected_step.action.value
        enqueued = self.executive.enqueue_bounded_action(selected_step, self.latest_world_state)
        self.recorder.emit(
            "JEV_DECISION",
            {
                "choice_id": choice.choice_id,
                "action": selected_step.action.value,
                "description": selected_option.description,
                "model": choice.model,
                "source": choice.source,
                "latency_ms": choice.latency_ms,
                "decision_index": self._decision_count,
                "enqueued": enqueued,
            },
        )
        if not enqueued:
            self._finish_decision_loop("failed")

    def _current_action_options(self) -> list[BoundedActionOption]:
        remaining_steps = [
            step
            for step in self._planner_script_steps
            if step.step_id not in self._released_step_ids
        ]
        cooled_down_keys = active_cooldown_keys(self._failure_cooldowns, monotonic())
        return build_action_options(
            remaining_steps,
            cooled_down_keys=cooled_down_keys,
            safety_flags=self.latest_safety_flags,
        )

    def _current_observation(self) -> dict[str, object]:
        remaining_action_count = len(
            [
                step
                for step in self._planner_script_steps
                if step.step_id not in self._released_step_ids
            ]
        )
        return compact_observation(
            objective=self._loop_objective or "",
            instruction=self._loop_instruction or "",
            world_state=self.latest_world_state,
            safety_flags=self.latest_safety_flags,
            recent_results=self._recent_action_results,
            completed_action_count=self._completed_steps_seen,
            remaining_action_count=remaining_action_count,
        )

    def _sync_completed_action_results(self) -> None:
        completed_steps = self.executive.current_state().completed_steps
        if len(completed_steps) <= self._completed_steps_seen:
            return
        for result in completed_steps[self._completed_steps_seen :]:
            action_label = self._pending_action_labels.pop(result.step_id, result.step_id)
            self._recent_action_results.append({"action": action_label, "result": "done"})
            self._consecutive_decision_failures = 0
        self._recent_action_results = self._recent_action_results[-5:]
        self._completed_steps_seen = len(completed_steps)

    def _handle_decision_failure(self) -> None:
        mission_state = self.executive.current_state()
        failed_step = mission_state.failed_step
        if failed_step is None or mission_state.plan is None:
            return
        matched_step = next(
            (step for step in mission_state.plan.steps if step.step_id == failed_step.step_id),
            None,
        )
        action_name = self._pending_action_labels.get(failed_step.step_id, failed_step.step_id)
        failure_policy = OnFailurePolicy.STOP
        if matched_step is not None:
            action_name = matched_step.action.value
            failure_policy = matched_step.on_failure
            self._failure_cooldowns[action_key(matched_step)] = monotonic() + JEV_FAILURE_COOLDOWN_S
        self._recent_action_results.append(
            {"action": action_name, "result": failed_step.reason or "failed"}
        )
        self._recent_action_results = self._recent_action_results[-5:]
        self._consecutive_decision_failures += 1
        self.recorder.emit(
            "JEV_ACTION_FAILED",
            {
                "step_id": failed_step.step_id,
                "action": action_name,
                "reason": failed_step.reason,
                "consecutive_failures": self._consecutive_decision_failures,
            },
        )
        if (
            failure_policy == OnFailurePolicy.STOP
            or self._consecutive_decision_failures
            >= self.config.brain.jev_max_consecutive_failures
        ):
            self._finish_decision_loop("failed")
            return
        self.executive.release_failed_step_for_replan()

    def _finish_decision_loop(self, reason: str) -> None:
        if self._loop_phase != "running":
            return
        if reason == "completed":
            self._loop_phase = "done"
            self._loop_status_text = "done"
        else:
            self._loop_phase = "failed"
            self._loop_status_text = "failed"
            if reason != "estop":
                mission_state = self.executive.current_state()
                if (
                    mission_state.plan is not None
                    and mission_state.failed_step is None
                    and not self.executive.mission_loop_finished()
                ):
                    self.executive.cancel_with_stop()
        self.recorder.emit(
            "JEV_LOOP_DONE",
            {
                "reason": reason,
                "decision_count": self._decision_count,
                "model": self._jev_client.model,
                "source": self._jev_client.source,
            },
        )
        self._planner_ready = False

    def _decision_loop_status(self) -> dict[str, object]:
        status_text = self._loop_status_text
        if self.config.brain.jev_enabled and self._loop_phase == "running":
            status_text = JEV_RUNNING_STATUS
        return {
            "enabled": self.config.brain.jev_enabled,
            "phase": self._loop_phase,
            "status_text": status_text,
            "model": self._jev_client.model,
            "source": self._jev_client.source,
            "decision_count": self._decision_count,
            "selected_action": self._selected_action_description,
            "objective": self._loop_objective,
        }

    def _clear_decision_loop_state(self) -> None:
        self._planner_script_steps = []
        self._planner_ready = False
        self._released_step_ids = set()
        self._decision_count = 0
        self._consecutive_decision_failures = 0
        self._recent_action_results = []
        self._failure_cooldowns = {}
        self._completed_steps_seen = 0
        self._pending_action_labels = {}
        self._loop_phase = "idle"
        self._loop_status_text = "idle"
        self._loop_objective = None
        self._loop_instruction = None
        self._selected_action_description = None

    async def _cancel_task(self, task: asyncio.Task[None] | None) -> None:
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return

    async def _control_loop(self) -> None:
        loop_period_s = 1.0 / float(self.config.control.flycore_hz)
        while self._running:
            loop_start_time_s = monotonic()
            observation = self.body_adapter.high_rate_observation()
            world_snapshot = self.body_adapter.world_snapshot()
            self.latest_world_state = self.resolver.resolve_world_state(
                robot_pose=observation.pose,
                world_snapshot=world_snapshot,
            )
            active_intent = self.executive.tick(self.latest_world_state, observation.contacts)
            if active_intent is not None and active_intent.intent_id != self._active_intent_id:
                self.flycore.set_intent(active_intent)
                self._active_intent_id = active_intent.intent_id
                self.recorder.emit(
                    "INTENT_ISSUED",
                    {
                        "intent_id": active_intent.intent_id,
                        "step_id": active_intent.step_id,
                        "mode": active_intent.mode.value,
                        "ttl_ms": active_intent.ttl_ms,
                    },
                )

            motor_command = self.flycore.tick(observation, dt_s=loop_period_s)
            safety_outcome = self.safety_kernel.enforce(motor_command, observation)
            self.latest_safety_flags = safety_outcome.active_flags
            body_state = await self.body_adapter.step(
                safety_outcome.clamped_command, dt_s=loop_period_s
            )
            self.latest_body_state = body_state.model_dump(mode="json")

            recording_enabled = self._is_recording_enabled()
            if self.config.simulation.render_operator_camera or recording_enabled:
                self.latest_frame = self.body_adapter.low_rate_frame()
            if recording_enabled:
                self._persist_recording_frame(self.latest_frame)

            elapsed_s = monotonic() - loop_start_time_s
            sleep_duration_s = max(0.0, loop_period_s - elapsed_s)
            await asyncio.sleep(sleep_duration_s)

    async def scripted_waypoint_mission(self) -> None:
        await self.submit_instruction("Walk to the red cube.")

    async def manual_intent(self, motor_command: MotorCommand, dt_s: float) -> dict[str, Any]:
        observation = self.body_adapter.high_rate_observation()
        safety_outcome = self.safety_kernel.enforce(motor_command, observation)
        body_state = await self.body_adapter.step(safety_outcome.clamped_command, dt_s=dt_s)
        self.latest_body_state = body_state.model_dump(mode="json")
        return self.latest_body_state

    def set_recording(self, enabled: bool) -> None:
        self._recording_override = enabled
        if enabled and self.recorder.output_path is None:
            recording_output_dir = self._ensure_recording_output_dir()
            self.recorder.output_path = recording_output_dir / "telemetry.jsonl"
        self.recorder.emit("RECORDING_CHANGED", {"enabled": enabled, "source": "api_recording"})

    def _is_recording_enabled(self) -> bool:
        mission_state = self.executive.current_state()
        return (
            self.config.safety.record_images_by_default
            or self._session_recording_enabled
            or self._recording_override
            or mission_state.recording_enabled
        )

    def _ensure_recording_output_dir(self) -> Path:
        if self._recording_output_dir is None:
            self._recording_output_dir = self._recordings_root / self._recording_session_id
            self._recording_frames_dir = self._recording_output_dir / "frames"
            self._recording_frames_dir.mkdir(parents=True, exist_ok=True)
        assert self._recording_output_dir is not None
        return self._recording_output_dir

    def _persist_recording_frame(self, frame: Frame) -> None:
        if frame.is_placeholder or frame.data_url is None:
            return
        if frame.timestamp_ns <= self._last_recorded_frame_ns:
            return
        if (
            self._last_recorded_frame_ns > 0
            and frame.timestamp_ns - self._last_recorded_frame_ns
            < self._recording_frame_interval_ns
        ):
            return
        frame_data_url_parts = frame.data_url.split(",", maxsplit=1)
        if len(frame_data_url_parts) != 2:
            return
        try:
            frame_bytes = base64.b64decode(frame_data_url_parts[1], validate=True)
        except (ValueError, binascii.Error):
            return
        recording_output_dir = self._ensure_recording_output_dir()
        assert self._recording_frames_dir is not None
        frame_filename = f"frame_{self._recording_frame_count:06d}.png"
        frame_path = self._recording_frames_dir / frame_filename
        frame_path.write_bytes(frame_bytes)
        self._recording_frame_count += 1
        self._last_recorded_frame_ns = frame.timestamp_ns
        if self.recorder.output_path is None:
            self.recorder.output_path = recording_output_dir / "telemetry.jsonl"
