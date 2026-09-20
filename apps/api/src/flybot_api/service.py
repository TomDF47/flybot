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
from flybot_core import FlyBotConfig, load_config
from flybot_core.config import EnvironmentSettings
from flybot_core.ids import new_identifier
from flybot_core.models import Frame, MotorCommand, Pose2D, WorldState
from flybot_executive import MissionExecutive
from flybot_flycore import L0FlyCoreController
from flybot_perception import ObjectTrackResolver
from flybot_safety import SafetyKernel
from flybot_telemetry import TelemetryRecorder


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
        self._running = False
        self._recording_override: bool = False

    async def start(self) -> None:
        await self.reset(seed=self.config.simulation.seed)
        self._running = True
        self._control_task = asyncio.create_task(self._control_loop(), name="flybot-control-loop")

    async def stop(self) -> None:
        self._running = False
        if self._control_task is not None:
            self._control_task.cancel()
            try:
                await self._control_task
            except asyncio.CancelledError:
                pass

    async def e_stop(self) -> None:
        self.safety_kernel.set_estop_latched(True)
        await self.body_adapter.safe_stop()
        self.recorder.emit("SAFETY_STOP", {"source": "api_estop"})

    async def reset(self, seed: int) -> None:
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
        self.executive.set_plan(plan)
        self.recorder.emit(
            "PLAN_ACCEPTED", {"mission_id": plan.mission_id, "step_count": len(plan.steps)}
        )
        return plan.mission_id

    def status(self, *, include_frame_data_url: bool = True) -> RuntimeStatus:
        mission_state = self.executive.current_state()
        recording_enabled = self._is_recording_enabled()
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
            },
            world_state=self.latest_world_state,
            body_state=self.latest_body_state,
            controller_state=self.flycore.status().model_dump(mode="json"),
            backend=self.config.simulation.backend,
        )

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
