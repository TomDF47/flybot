from __future__ import annotations

import math
from dataclasses import dataclass, field
from time import monotonic
from typing import Any

from flybot_core.config import FlyBotConfig
from flybot_core.ids import monotonic_time_ns, new_identifier
from flybot_core.models import (
    ContactSample,
    IntentCommand,
    IntentMode,
    MissionPlan,
    MotionConstraints,
    PlanAction,
    PlanStep,
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
    active_step_context: dict[str, object] = field(default_factory=dict)
    timeline: list[dict[str, object]] = field(default_factory=list)
    recording_enabled: bool = False
    change_events_count: int = 0
    follow_min_distance_observed: float = 999.0
    touch_contact_count: int = 0
    touch_target_object_id: str | None = None

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
        self._append_timeline(
            "PLAN_ACCEPTED", {"mission_id": plan.mission_id, "step_count": len(plan.steps)}
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

    def tick(self, world_state: WorldState, contacts: list[ContactSample]) -> IntentCommand | None:
        if self._state.plan is None:
            return None
        if self._state.failed_step is not None:
            return self._state.active_intent
        if self._state.active_step_index >= len(self._state.plan.steps):
            return self._state.active_intent

        active_step = self._state.plan.steps[self._state.active_step_index]
        if self._state.step_started_at_s is None:
            self._start_active_step(active_step, world_state)
            return self._state.active_intent

        assert self._state.step_started_at_s is not None
        elapsed_s = monotonic() - self._state.step_started_at_s
        if elapsed_s > active_step.timeout_s:
            self._fail_active_step(step_id=active_step.step_id, reason="STEP_TIMEOUT")
            return self._state.active_intent

        if self._tick_active_step(active_step, world_state, contacts, elapsed_s):
            self._complete_active_step(active_step)
            if self._state.active_step_index >= len(self._state.plan.steps):
                assert self._state.plan is not None
                self._state.active_intent = self._new_intent(
                    mission_id=self._state.plan.mission_id,
                    step_id="mission_complete",
                    mode=IntentMode.STOP,
                    desired_speed=0.0,
                    stand_off_body_lengths=0.0,
                )
                self._append_timeline(
                    "MISSION_COMPLETED", {"mission_id": self._state.plan.mission_id}
                )
                return self._state.active_intent
            next_step = self._state.plan.steps[self._state.active_step_index]
            self._start_active_step(next_step, world_state)
            return self._state.active_intent

        if (
            self._state.active_intent is not None
            and self._state.active_intent.mode != IntentMode.STOP
        ):
            self._state.active_intent.issued_at_ns = monotonic_time_ns()

        return self._state.active_intent

    def current_state(self) -> MissionState:
        return self._state

    def _start_active_step(self, active_step: PlanStep, world_state: WorldState) -> None:
        assert self._state.plan is not None
        self._state.step_started_at_s = monotonic()
        self._state.active_step_context = self._build_step_context(
            action=active_step.action,
            target=active_step.target,
            parameters=active_step.parameters,
            world_state=world_state,
        )
        self._state.active_intent = self._create_intent_for_step(
            active_step.action,
            active_step.target,
            active_step.parameters,
            active_step.step_id,
            self._state.plan.mission_id,
            world_state,
            self._state.active_step_context,
        )
        self._append_timeline(
            "STEP_STARTED",
            {"step_id": active_step.step_id, "action": active_step.action.value},
        )
        if active_step.action == PlanAction.OBSERVE:
            allow_recording = bool(active_step.parameters.get("allow_recording", False))
            self._state.recording_enabled = allow_recording
            if not allow_recording:
                self._append_timeline(
                    "RECORDING_CHANGED",
                    {"enabled": False, "reason": "observe_step_default_off"},
                )

    def _tick_active_step(
        self,
        active_step: PlanStep,
        world_state: WorldState,
        contacts: list[ContactSample],
        elapsed_s: float,
    ) -> bool:
        if active_step.action == PlanAction.PATROL:
            return self._tick_patrol(active_step, world_state)
        if active_step.action == PlanAction.OBSERVE:
            return self._tick_observe(active_step, world_state, elapsed_s)
        if active_step.action == PlanAction.FOLLOW:
            return self._tick_follow(active_step, world_state, elapsed_s)
        if active_step.action in {PlanAction.TOUCH, PlanAction.NUDGE}:
            return self._tick_touch_or_nudge(active_step, world_state, contacts)
        if active_step.action == PlanAction.INSPECT:
            return self._tick_inspect(active_step, world_state)
        if active_step.action == PlanAction.WAIT:
            wait_duration_s = self._float_parameter(
                active_step.parameters, "duration_s", default_value=2.0
            )
            return elapsed_s >= wait_duration_s
        return self._is_step_completed(
            active_step.action, active_step.target, active_step.parameters, world_state
        )

    def _tick_patrol(self, active_step: PlanStep, world_state: WorldState) -> bool:
        if world_state.robot_pose is None:
            return False
        waypoints = self._state.active_step_context.get("waypoints")
        if not isinstance(waypoints, list) or not waypoints:
            return True
        waypoint_index = self._int_context_value("waypoint_index", default_value=0)
        if waypoint_index >= len(waypoints):
            return True
        waypoint_pose = waypoints[waypoint_index]
        assert isinstance(waypoint_pose, Pose2D)
        distance = math.hypot(
            waypoint_pose.x - world_state.robot_pose.x,
            waypoint_pose.y - world_state.robot_pose.y,
        )
        stand_off = self._float_parameter(
            active_step.parameters, "stand_off_body_lengths", default_value=0.45
        )
        if distance <= stand_off:
            waypoint_index += 1
            self._state.active_step_context["waypoint_index"] = waypoint_index
            self._append_timeline(
                "PATROL_WAYPOINT_REACHED",
                {"step_id": active_step.step_id, "waypoint_index": waypoint_index},
            )
            if waypoint_index >= len(waypoints):
                return True
            next_waypoint_pose = waypoints[waypoint_index]
            assert isinstance(next_waypoint_pose, Pose2D)
            self._state.active_intent = self._new_intent(
                mission_id=self._require_mission_id(),
                step_id=active_step.step_id,
                mode=IntentMode.NAVIGATE,
                target_pose=next_waypoint_pose,
                desired_speed=self._float_parameter(
                    active_step.parameters, "speed", default_value=0.55
                ),
                stand_off_body_lengths=stand_off,
            )
        return False

    def _tick_observe(
        self, active_step: PlanStep, world_state: WorldState, elapsed_s: float
    ) -> bool:
        baseline_positions = self._state.active_step_context.get("baseline_positions")
        changed_object_ids = self._state.active_step_context.get("changed_object_ids")
        if isinstance(baseline_positions, dict) and isinstance(changed_object_ids, set):
            for track in world_state.objects:
                baseline = baseline_positions.get(track.track_id)
                if baseline is None or track.world_pose is None:
                    continue
                baseline_x, baseline_y = baseline
                displacement = math.hypot(
                    track.world_pose.x - baseline_x, track.world_pose.y - baseline_y
                )
                if displacement > 0.3:
                    changed_object_ids.add(track.track_id)
        dwell_s = self._float_parameter(active_step.parameters, "dwell_s", default_value=2.0)
        if elapsed_s >= dwell_s:
            changed_count = len(changed_object_ids) if isinstance(changed_object_ids, set) else 0
            self._state.change_events_count += changed_count
            self._append_timeline(
                "OBSERVE_COMPLETED",
                {"step_id": active_step.step_id, "change_events_count": changed_count},
            )
            self._state.recording_enabled = False
            return True
        return False

    def _tick_follow(
        self, active_step: PlanStep, world_state: WorldState, elapsed_s: float
    ) -> bool:
        target_track = self._resolver.resolve_target(world_state, active_step.target)
        if target_track is None or target_track.world_pose is None:
            missing_ticks = self._int_context_value("missing_ticks", default_value=0) + 1
            self._state.active_step_context["missing_ticks"] = missing_ticks
            allowed_missing_ticks = max(1, int(self._config.control.target_stale_ms / 100))
            if missing_ticks > allowed_missing_ticks:
                self._fail_active_step(step_id=active_step.step_id, reason="TARGET_LOST")
                return False
            self._state.active_intent = self._new_intent(
                mission_id=self._require_mission_id(),
                step_id=active_step.step_id,
                mode=IntentMode.WAIT,
                desired_speed=0.0,
                stand_off_body_lengths=0.0,
            )
            return False

        self._state.active_step_context["missing_ticks"] = 0
        assert world_state.robot_pose is not None
        distance = math.hypot(
            target_track.world_pose.x - world_state.robot_pose.x,
            target_track.world_pose.y - world_state.robot_pose.y,
        )
        self._state.follow_min_distance_observed = min(
            self._state.follow_min_distance_observed, distance
        )
        min_distance = self._float_parameter(
            active_step.parameters, "min_distance_body_lengths", default_value=1.0
        )
        max_distance = self._float_parameter(
            active_step.parameters, "max_distance_body_lengths", default_value=1.8
        )
        desired_speed = self._float_parameter(active_step.parameters, "speed", default_value=0.5)
        if distance < min_distance:
            intent_mode = IntentMode.RETREAT
        elif distance > max_distance:
            intent_mode = IntentMode.FOLLOW
        else:
            intent_mode = IntentMode.FOLLOW
            desired_speed = 0.0
        self._state.active_intent = self._new_intent(
            mission_id=self._require_mission_id(),
            step_id=active_step.step_id,
            mode=intent_mode,
            target_pose=target_track.world_pose,
            target_track_id=target_track.track_id,
            desired_speed=desired_speed,
            stand_off_body_lengths=min_distance,
            min_distance_body_lengths=min_distance,
            max_distance_body_lengths=max_distance,
        )
        duration_s = self._float_parameter(active_step.parameters, "duration_s", default_value=8.0)
        return elapsed_s >= duration_s

    def _tick_touch_or_nudge(
        self,
        active_step: PlanStep,
        world_state: WorldState,
        contacts: list[ContactSample],
    ) -> bool:
        target_track = self._resolver.resolve_target(world_state, active_step.target)
        if target_track is None or target_track.world_pose is None:
            self._fail_active_step(step_id=active_step.step_id, reason="TARGET_LOST")
            return False
        phase = str(self._state.active_step_context.get("phase", "approach"))
        max_force = self._float_parameter(active_step.parameters, "max_force", default_value=0.35)
        retract_distance = self._float_parameter(
            active_step.parameters, "retract_distance", default_value=0.8
        )
        if phase == "approach":
            self._state.active_intent = self._new_intent(
                mission_id=self._require_mission_id(),
                step_id=active_step.step_id,
                mode=IntentMode.APPROACH,
                target_pose=target_track.world_pose,
                target_track_id=target_track.track_id,
                desired_speed=self._float_parameter(
                    active_step.parameters, "speed", default_value=0.45
                ),
                stand_off_body_lengths=self._float_parameter(
                    active_step.parameters, "stand_off_body_lengths", default_value=0.25
                ),
            )
        elif phase == "retract":
            retract_start = self._state.active_step_context.get("retract_start")
            if isinstance(retract_start, tuple) and world_state.robot_pose is not None:
                start_x, start_y = retract_start
                moved_distance = math.hypot(
                    world_state.robot_pose.x - start_x, world_state.robot_pose.y - start_y
                )
                if moved_distance >= retract_distance:
                    return True

        target_contacts = [
            sample for sample in contacts if sample.object_id == target_track.track_id
        ]
        if any(sample.force > max_force for sample in target_contacts):
            self._fail_active_step(step_id=active_step.step_id, reason="FORCE_LIMIT_EXCEEDED")
            return False
        if phase == "approach" and target_contacts:
            self._state.touch_contact_count += 1
            self._state.touch_target_object_id = target_track.track_id
            self._state.active_step_context["phase"] = "retract"
            assert world_state.robot_pose is not None
            self._state.active_step_context["retract_start"] = (
                world_state.robot_pose.x,
                world_state.robot_pose.y,
            )
            self._append_timeline(
                "CONTACT",
                {
                    "step_id": active_step.step_id,
                    "object_id": target_track.track_id,
                    "force": target_contacts[0].force,
                },
            )
            self._state.active_intent = self._new_intent(
                mission_id=self._require_mission_id(),
                step_id=active_step.step_id,
                mode=IntentMode.RETREAT,
                desired_speed=0.5,
                stand_off_body_lengths=0.0,
            )
        return False

    def _tick_inspect(self, active_step: PlanStep, world_state: WorldState) -> bool:
        if world_state.robot_pose is None:
            return False
        inspect_waypoints = self._state.active_step_context.get("inspect_waypoints")
        if not isinstance(inspect_waypoints, list) or not inspect_waypoints:
            return True
        inspect_index = self._int_context_value("inspect_index", default_value=0)
        if inspect_index >= len(inspect_waypoints):
            return True
        current_viewpoint = inspect_waypoints[inspect_index]
        assert isinstance(current_viewpoint, Pose2D)
        distance = math.hypot(
            current_viewpoint.x - world_state.robot_pose.x,
            current_viewpoint.y - world_state.robot_pose.y,
        )
        stand_off = self._float_parameter(
            active_step.parameters, "stand_off_body_lengths", default_value=0.4
        )
        if distance <= stand_off:
            inspect_index += 1
            self._state.active_step_context["inspect_index"] = inspect_index
            self._append_timeline(
                "INSPECT_VIEWPOINT_REACHED",
                {"step_id": active_step.step_id, "viewpoint_index": inspect_index},
            )
            if inspect_index >= len(inspect_waypoints):
                return True
            next_viewpoint = inspect_waypoints[inspect_index]
            assert isinstance(next_viewpoint, Pose2D)
            self._state.active_intent = self._new_intent(
                mission_id=self._require_mission_id(),
                step_id=active_step.step_id,
                mode=IntentMode.NAVIGATE,
                target_pose=next_viewpoint,
                desired_speed=self._float_parameter(
                    active_step.parameters, "speed", default_value=0.45
                ),
                stand_off_body_lengths=stand_off,
            )
        return False

    def _create_intent_for_step(
        self,
        action: PlanAction,
        target: TargetSpec | None,
        parameters: dict[str, object],
        step_id: str,
        mission_id: str,
        world_state: WorldState,
        step_context: dict[str, object],
    ) -> IntentCommand:
        if action == PlanAction.STOP:
            return self._new_intent(
                mission_id=mission_id,
                step_id=step_id,
                mode=IntentMode.STOP,
                desired_speed=0.0,
                stand_off_body_lengths=0.0,
            )

        if action == PlanAction.PATROL:
            waypoints = step_context.get("waypoints")
            if isinstance(waypoints, list) and waypoints:
                first_waypoint = waypoints[0]
                assert isinstance(first_waypoint, Pose2D)
                return self._new_intent(
                    mission_id=mission_id,
                    step_id=step_id,
                    mode=IntentMode.NAVIGATE,
                    target_pose=first_waypoint,
                    desired_speed=self._float_parameter(parameters, "speed", default_value=0.55),
                    stand_off_body_lengths=self._float_parameter(
                        parameters, "stand_off_body_lengths", default_value=0.45
                    ),
                )
        if action == PlanAction.OBSERVE:
            return self._new_intent(
                mission_id=mission_id,
                step_id=step_id,
                mode=IntentMode.WAIT,
                desired_speed=0.0,
                stand_off_body_lengths=0.0,
            )

        if action == PlanAction.INSPECT:
            inspect_waypoints = step_context.get("inspect_waypoints")
            if isinstance(inspect_waypoints, list) and inspect_waypoints:
                first_inspect_waypoint = inspect_waypoints[0]
                assert isinstance(first_inspect_waypoint, Pose2D)
                return self._new_intent(
                    mission_id=mission_id,
                    step_id=step_id,
                    mode=IntentMode.NAVIGATE,
                    target_pose=first_inspect_waypoint,
                    desired_speed=self._float_parameter(parameters, "speed", default_value=0.45),
                    stand_off_body_lengths=self._float_parameter(
                        parameters, "stand_off_body_lengths", default_value=0.4
                    ),
                )

        mode_map = {
            PlanAction.NAVIGATE: IntentMode.NAVIGATE,
            PlanAction.APPROACH: IntentMode.APPROACH,
            PlanAction.RETREAT: IntentMode.RETREAT,
            PlanAction.FOLLOW: IntentMode.FOLLOW,
            PlanAction.RETURN_HOME: IntentMode.RETURN_HOME,
            PlanAction.WAIT: IntentMode.WAIT,
            PlanAction.TOUCH: IntentMode.APPROACH,
            PlanAction.NUDGE: IntentMode.APPROACH,
        }
        intent_mode = mode_map.get(action, IntentMode.NAVIGATE)
        target_track = self._resolver.resolve_target(world_state, target)
        target_pose: Pose2D | None = target_track.world_pose if target_track is not None else None
        if action == PlanAction.RETURN_HOME:
            target_pose = world_state.home_pose
        desired_speed = self._float_parameter(parameters, "speed", default_value=0.5)
        stand_off = self._float_parameter(parameters, "stand_off_body_lengths", default_value=0.8)
        min_distance = self._float_parameter(
            parameters, "min_distance_body_lengths", default_value=0.0
        )
        max_distance = self._float_parameter(
            parameters, "max_distance_body_lengths", default_value=2.0
        )
        return self._new_intent(
            mission_id=mission_id,
            step_id=step_id,
            mode=intent_mode,
            target_track_id=target_track.track_id if target_track else None,
            target_pose=target_pose,
            desired_speed=desired_speed,
            stand_off_body_lengths=stand_off,
            min_distance_body_lengths=min_distance,
            max_distance_body_lengths=max_distance,
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
        if action in {
            PlanAction.WAIT,
            PlanAction.PATROL,
            PlanAction.OBSERVE,
            PlanAction.INSPECT,
            PlanAction.FOLLOW,
            PlanAction.TOUCH,
            PlanAction.NUDGE,
        }:
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
        stand_off = self._float_parameter(parameters, "stand_off_body_lengths", default_value=0.8)
        return distance <= stand_off

    def _build_step_context(
        self,
        action: PlanAction,
        target: TargetSpec | None,
        parameters: dict[str, object],
        world_state: WorldState,
    ) -> dict[str, object]:
        if action == PlanAction.PATROL:
            return {
                "waypoints": self._parse_waypoints(parameters.get("waypoints")),
                "waypoint_index": 0,
            }
        if action == PlanAction.OBSERVE:
            baseline_positions: dict[str, tuple[float, float]] = {}
            for track in world_state.objects:
                if track.world_pose is None:
                    continue
                baseline_positions[track.track_id] = (track.world_pose.x, track.world_pose.y)
            return {
                "baseline_positions": baseline_positions,
                "changed_object_ids": set(),
            }
        if action == PlanAction.FOLLOW:
            return {"missing_ticks": 0}
        if action in {PlanAction.TOUCH, PlanAction.NUDGE}:
            return {"phase": "approach"}
        if action == PlanAction.INSPECT:
            target_track = self._resolver.resolve_target(world_state, target)
            if target_track is None or target_track.world_pose is None:
                return {"inspect_waypoints": [], "inspect_index": 0}
            viewpoints = int(
                max(2, self._float_parameter(parameters, "viewpoints", default_value=4.0))
            )
            radius = self._float_parameter(parameters, "radius_body_lengths", default_value=1.0)
            inspect_waypoints: list[Pose2D] = []
            if viewpoints == 2:
                inspect_angles = [0.0, 1.2]
            else:
                inspect_angles = [
                    (2.0 * math.pi * index) / viewpoints for index in range(viewpoints)
                ]
            for angle in inspect_angles:
                inspect_waypoints.append(
                    Pose2D(
                        x=target_track.world_pose.x + (math.cos(angle) * radius),
                        y=target_track.world_pose.y + (math.sin(angle) * radius),
                        heading_rad=angle,
                    )
                )
            return {"inspect_waypoints": inspect_waypoints, "inspect_index": 0}
        return {}

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

    @staticmethod
    def _parse_waypoints(raw_waypoints: object) -> list[Pose2D]:
        if not isinstance(raw_waypoints, list):
            return []
        parsed_waypoints: list[Pose2D] = []
        for waypoint in raw_waypoints:
            if not isinstance(waypoint, list | tuple):
                continue
            if len(waypoint) < 2:
                continue
            coordinate_x = waypoint[0]
            coordinate_y = waypoint[1]
            if isinstance(coordinate_x, int | float) and isinstance(coordinate_y, int | float):
                parsed_waypoints.append(
                    Pose2D(x=float(coordinate_x), y=float(coordinate_y), heading_rad=0.0)
                )
        return parsed_waypoints

    def _new_intent(
        self,
        mission_id: str,
        step_id: str,
        mode: IntentMode,
        target_pose: Pose2D | None = None,
        target_track_id: str | None = None,
        desired_speed: float = 0.5,
        stand_off_body_lengths: float = 0.8,
        min_distance_body_lengths: float | None = None,
        max_distance_body_lengths: float | None = None,
    ) -> IntentCommand:
        constraints = self._default_constraints()
        constraints.min_distance_body_lengths = min_distance_body_lengths
        constraints.max_distance_body_lengths = max_distance_body_lengths
        return IntentCommand(
            intent_id=new_identifier("intent"),
            mission_id=mission_id,
            step_id=step_id,
            mode=mode,
            target_track_id=target_track_id,
            target_pose=target_pose,
            desired_speed=desired_speed,
            stand_off_body_lengths=stand_off_body_lengths,
            constraints=constraints,
            ttl_ms=self._config.control.intent_ttl_ms,
            issued_at_ns=monotonic_time_ns(),
        )

    def _int_context_value(self, key: str, default_value: int) -> int:
        raw_value = self._state.active_step_context.get(key, default_value)
        if isinstance(raw_value, int):
            return raw_value
        if isinstance(raw_value, float):
            return int(raw_value)
        if isinstance(raw_value, str):
            try:
                return int(raw_value)
            except ValueError:
                return default_value
        return default_value

    def _complete_active_step(self, completed_step: PlanStep) -> None:
        assert self._state.plan is not None
        metrics: dict[str, float] = {}
        if completed_step.action == PlanAction.OBSERVE:
            changed_object_ids = self._state.active_step_context.get("changed_object_ids")
            if isinstance(changed_object_ids, set):
                metrics["change_events_count"] = float(len(changed_object_ids))
        if completed_step.action == PlanAction.FOLLOW:
            metrics["min_distance_observed"] = float(self._state.follow_min_distance_observed)
        if completed_step.action in {PlanAction.TOUCH, PlanAction.NUDGE}:
            metrics["touch_contact_count"] = float(self._state.touch_contact_count)
        self._state.completed_steps.append(
            StepResult(
                mission_id=self._state.plan.mission_id,
                step_id=completed_step.step_id,
                status=StepStatus.COMPLETED,
                metrics=metrics,
            )
        )
        self._append_timeline(
            "STEP_COMPLETED",
            {"step_id": completed_step.step_id, "action": completed_step.action.value},
        )
        self._state.active_step_index += 1
        self._state.step_started_at_s = None
        self._state.active_step_context = {}
        self._state.recording_enabled = False

    def _fail_active_step(self, step_id: str, reason: str) -> None:
        mission_id = self._require_mission_id()
        self._state.failed_step = StepResult(
            mission_id=mission_id,
            step_id=step_id,
            status=StepStatus.FAILED,
            reason=reason,
        )
        self._state.active_intent = self._new_intent(
            mission_id=mission_id,
            step_id=step_id,
            mode=IntentMode.STOP,
            desired_speed=0.0,
            stand_off_body_lengths=0.0,
        )
        self._append_timeline("STEP_FAILED", {"step_id": step_id, "reason": reason})

    def _append_timeline(self, event_type: str, payload: dict[str, Any]) -> None:
        self._state.timeline.append(
            {"event": event_type, "timestamp_ns": monotonic_time_ns(), "payload": payload}
        )

    def _require_mission_id(self) -> str:
        if self._state.plan is None:
            raise RuntimeError("Mission state has no active plan")
        return self._state.plan.mission_id
