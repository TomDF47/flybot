from __future__ import annotations

import math
from dataclasses import dataclass, field
from time import monotonic

from flybot_core.config import FlyBotConfig
from flybot_core.ids import monotonic_time_ns, new_identifier
from flybot_core.models import (
    IntentCommand,
    IntentMode,
    MissionPlan,
    MotionConstraints,
    PlanAction,
    Pose2D,
    StepResult,
    StepStatus,
    TargetSpec,
    WorldState,
)
from flybot_perception.resolver import ObjectTrackResolver


@dataclass
class MissionState:
    mission_id: str | None = None
    plan: MissionPlan | None = None
    active_step_index: int = 0
    step_started_at_s: float | None = None
    completed_steps: list[StepResult] = field(default_factory=list)
    failed_step: StepResult | None = None
    active_intent: IntentCommand | None = None

    @property
    def is_active(self) -> bool:
        return (
            self.plan is not None
            and self.failed_step is None
            and self.active_step_index < len(self.plan.steps)
        )


class MissionExecutive:
    def __init__(self, config: FlyBotConfig, resolver: ObjectTrackResolver) -> None:
        self._config = config
        self._resolver = resolver
        self._state = MissionState()

    def set_plan(self, plan: MissionPlan) -> MissionState:
        self._state = MissionState(
            mission_id=plan.mission_id,
            plan=plan,
            active_step_index=0,
            step_started_at_s=None,
            completed_steps=[],
            failed_step=None,
            active_intent=None,
        )
        return self._state

    def cancel_with_stop(self) -> None:
        if self._state.plan is None:
            return
        stop_intent = IntentCommand(
            intent_id=new_identifier("intent"),
            mission_id=self._state.plan.mission_id,
            step_id="cancel",
            mode=IntentMode.STOP,
            constraints=self._default_constraints(),
            ttl_ms=self._config.control.intent_ttl_ms,
            issued_at_ns=monotonic_time_ns(),
        )
        self._state.active_intent = stop_intent

    def tick(self, world_state: WorldState) -> IntentCommand | None:
        if self._state.plan is None:
            return None
        if self._state.failed_step is not None:
            return self._state.active_intent
        if self._state.active_step_index >= len(self._state.plan.steps):
            return self._state.active_intent

        active_step = self._state.plan.steps[self._state.active_step_index]
        if self._state.step_started_at_s is None:
            self._state.step_started_at_s = monotonic()
            self._state.active_intent = self._create_intent_for_step(
                active_step.action,
                active_step.target,
                active_step.parameters,
                active_step.step_id,
                self._state.plan.mission_id,
                world_state,
            )
            return self._state.active_intent

        assert self._state.step_started_at_s is not None
        elapsed_s = monotonic() - self._state.step_started_at_s
        if elapsed_s > active_step.timeout_s:
            self._state.failed_step = StepResult(
                mission_id=self._state.plan.mission_id,
                step_id=active_step.step_id,
                status=StepStatus.FAILED,
                reason="STEP_TIMEOUT",
            )
            self._state.active_intent = IntentCommand(
                intent_id=new_identifier("intent"),
                mission_id=self._state.plan.mission_id,
                step_id=active_step.step_id,
                mode=IntentMode.STOP,
                constraints=self._default_constraints(),
                ttl_ms=self._config.control.intent_ttl_ms,
                issued_at_ns=monotonic_time_ns(),
            )
            return self._state.active_intent

        if self._is_step_completed(
            active_step.action, active_step.target, active_step.parameters, world_state
        ):
            self._state.completed_steps.append(
                StepResult(
                    mission_id=self._state.plan.mission_id,
                    step_id=active_step.step_id,
                    status=StepStatus.COMPLETED,
                )
            )
            self._state.active_step_index += 1
            self._state.step_started_at_s = None
            if self._state.active_step_index >= len(self._state.plan.steps):
                self._state.active_intent = IntentCommand(
                    intent_id=new_identifier("intent"),
                    mission_id=self._state.plan.mission_id,
                    step_id="mission_complete",
                    mode=IntentMode.STOP,
                    constraints=self._default_constraints(),
                    ttl_ms=self._config.control.intent_ttl_ms,
                    issued_at_ns=monotonic_time_ns(),
                )
                return self._state.active_intent
            next_step = self._state.plan.steps[self._state.active_step_index]
            self._state.active_intent = self._create_intent_for_step(
                next_step.action,
                next_step.target,
                next_step.parameters,
                next_step.step_id,
                self._state.plan.mission_id,
                world_state,
            )
            self._state.step_started_at_s = monotonic()
            return self._state.active_intent

        if (
            self._state.active_intent is not None
            and self._state.active_intent.mode != IntentMode.STOP
        ):
            self._state.active_intent.issued_at_ns = monotonic_time_ns()

        return self._state.active_intent

    def current_state(self) -> MissionState:
        return self._state

    def _create_intent_for_step(
        self,
        action: PlanAction,
        target: TargetSpec | None,
        parameters: dict[str, object],
        step_id: str,
        mission_id: str,
        world_state: WorldState,
    ) -> IntentCommand:
        if action == PlanAction.STOP:
            return IntentCommand(
                intent_id=new_identifier("intent"),
                mission_id=mission_id,
                step_id=step_id,
                mode=IntentMode.STOP,
                constraints=self._default_constraints(),
                ttl_ms=self._config.control.intent_ttl_ms,
                issued_at_ns=monotonic_time_ns(),
            )

        mode_map = {
            PlanAction.NAVIGATE: IntentMode.NAVIGATE,
            PlanAction.APPROACH: IntentMode.APPROACH,
            PlanAction.RETREAT: IntentMode.RETREAT,
            PlanAction.FOLLOW: IntentMode.FOLLOW,
            PlanAction.RETURN_HOME: IntentMode.RETURN_HOME,
            PlanAction.WAIT: IntentMode.WAIT,
            PlanAction.TOUCH: IntentMode.TOUCH,
            PlanAction.NUDGE: IntentMode.NUDGE,
        }
        intent_mode = mode_map.get(action, IntentMode.NAVIGATE)
        target_track = self._resolver.resolve_target(world_state, target)
        target_pose: Pose2D | None = target_track.world_pose if target_track is not None else None
        if action == PlanAction.RETURN_HOME:
            target_pose = world_state.home_pose
        desired_speed = self._float_parameter(parameters, "speed", default_value=0.5)
        stand_off = self._float_parameter(
            parameters, "stand_off_body_lengths", default_value=0.8
        )
        return IntentCommand(
            intent_id=new_identifier("intent"),
            mission_id=mission_id,
            step_id=step_id,
            mode=intent_mode,
            target_track_id=target_track.track_id if target_track else None,
            target_pose=target_pose,
            desired_speed=desired_speed,
            stand_off_body_lengths=stand_off,
            constraints=self._default_constraints(),
            ttl_ms=self._config.control.intent_ttl_ms,
            issued_at_ns=monotonic_time_ns(),
        )

    def _is_step_completed(
        self,
        action: PlanAction,
        target: TargetSpec | None,
        parameters: dict[str, object],
        world_state: WorldState,
    ) -> bool:
        if action == PlanAction.STOP:
            return True
        if action in {PlanAction.WAIT, PlanAction.PATROL, PlanAction.OBSERVE, PlanAction.INSPECT}:
            return False
        if world_state.robot_pose is None:
            return False
        target_track = self._resolver.resolve_target(world_state, target)
        target_pose = target_track.world_pose if target_track is not None else None
        if action == PlanAction.RETURN_HOME:
            target_pose = world_state.home_pose
        if target_pose is None:
            return False
        distance = math.hypot(
            target_pose.x - world_state.robot_pose.x,
            target_pose.y - world_state.robot_pose.y,
        )
        stand_off = self._float_parameter(
            parameters, "stand_off_body_lengths", default_value=0.8
        )
        return distance <= stand_off

    def _default_constraints(self) -> MotionConstraints:
        return MotionConstraints(
            max_speed_normalised=self._config.safety.max_speed_normalised,
            max_turn_normalised=self._config.safety.max_turn_normalised,
        )

    @staticmethod
    def _float_parameter(
        parameters: dict[str, object], parameter_name: str, default_value: float
    ) -> float:
        parameter_value = parameters.get(parameter_name, default_value)
        if isinstance(parameter_value, int | float):
            return float(parameter_value)
        if isinstance(parameter_value, str):
            try:
                return float(parameter_value)
            except ValueError:
                return default_value
        return default_value
