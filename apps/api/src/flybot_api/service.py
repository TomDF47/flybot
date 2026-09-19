from __future__ import annotations

import asyncio
from dataclasses import dataclass
from os import getenv
from pathlib import Path
from time import monotonic
from typing import Any

from flybot_body_flygym import create_body_adapter
from flybot_brain import create_cognitive_provider
from flybot_core import FlyBotConfig, load_config
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
        self.body_adapter = create_body_adapter(self.config)
        self.flycore = L0FlyCoreController()
        self.resolver = ObjectTrackResolver(
            use_ground_truth_for_tests=self.config.simulation.allow_ground_truth_for_tests
        )
        self.executive = MissionExecutive(config=self.config, resolver=self.resolver)
        self.safety_kernel = SafetyKernel(self.config.safety)
        telemetry_output = getenv("TELEMETRY_OUTPUT_JSONL", "").strip()
        self.recorder = TelemetryRecorder(
            output_path=Path(telemetry_output) if telemetry_output else None
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
        self.recorder.emit("SIM_RESET", {"seed": seed})

    async def submit_instruction(self, instruction: str) -> str:
        plan = await self.cognitive_provider.build_plan(instruction, self.latest_world_state)
        self.executive.set_plan(plan)
        self.recorder.emit(
            "PLAN_ACCEPTED", {"mission_id": plan.mission_id, "step_count": len(plan.steps)}
        )
        return plan.mission_id

    def status(self) -> RuntimeStatus:
        mission_state = self.executive.current_state()
        return RuntimeStatus(
            frame=self.latest_frame,
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
                "recording_enabled": mission_state.recording_enabled or self._recording_override,
                "change_events_count": mission_state.change_events_count,
                "follow_min_distance_observed": mission_state.follow_min_distance_observed,
                "touch_contact_count": mission_state.touch_contact_count,
                "touch_target_object_id": mission_state.touch_target_object_id,
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

            if self.config.simulation.render_operator_camera:
                self.latest_frame = self.body_adapter.low_rate_frame()

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
        self.recorder.emit("RECORDING_CHANGED", {"enabled": enabled, "source": "api_recording"})
