from __future__ import annotations

import math

from flybot_core.models import (
    ControllerStatus,
    HighRateObservation,
    IntentCommand,
    IntentMode,
    MotorCommand,
)


class L0FlyCoreController:
    def __init__(self) -> None:
        self._active_intent: IntentCommand | None = None
        self._status = ControllerStatus()

    def reset(self) -> None:
        self._active_intent = None
        self._status = ControllerStatus(mode=IntentMode.STOP, active_intent_id=None, detail="reset")

    def set_intent(self, intent_command: IntentCommand) -> None:
        self._active_intent = intent_command
        self._status = ControllerStatus(
            mode=intent_command.mode,
            active_intent_id=intent_command.intent_id,
            detail=f"intent:{intent_command.mode.value}",
        )

    def tick(self, high_rate_observation: HighRateObservation, dt_s: float) -> MotorCommand:
        if self._active_intent is None:
            self._status = ControllerStatus(mode=IntentMode.STOP, detail="no_intent")
            return MotorCommand(forward=0.0, turn=0.0)
        if self._active_intent.is_expired(high_rate_observation.timestamp_ns):
            self._status = ControllerStatus(mode=IntentMode.STOP, detail="intent_expired")
            return MotorCommand(forward=0.0, turn=0.0, stop=True)

        mode = self._active_intent.mode
        if mode == IntentMode.STOP:
            return MotorCommand(forward=0.0, turn=0.0, stop=True)
        if mode == IntentMode.TURN:
            return MotorCommand(
                forward=0.0, turn=self._clamp(self._active_intent.desired_speed or 0.35)
            )
        if mode == IntentMode.FORWARD:
            return MotorCommand(
                forward=self._clamp(self._active_intent.desired_speed or 0.5), turn=0.0
            )
        if mode == IntentMode.RETREAT:
            return MotorCommand(
                forward=-self._clamp(self._active_intent.desired_speed or 0.4), turn=0.0
            )
        if mode in {
            IntentMode.NAVIGATE,
            IntentMode.APPROACH,
            IntentMode.FOLLOW,
            IntentMode.RETURN_HOME,
        }:
            return self._navigate_like_command(high_rate_observation, dt_s)
        if mode in {IntentMode.WAIT, IntentMode.TOUCH, IntentMode.NUDGE}:
            return MotorCommand(forward=0.0, turn=0.0)
        return MotorCommand(forward=0.0, turn=0.0, stop=True)

    def status(self) -> ControllerStatus:
        return self._status

    def _navigate_like_command(
        self, high_rate_observation: HighRateObservation, dt_s: float
    ) -> MotorCommand:
        assert dt_s > 0.0
        if self._active_intent is None:
            return MotorCommand(forward=0.0, turn=0.0)

        target_pose = self._active_intent.target_pose
        if target_pose is None and self._active_intent.desired_heading_rad is None:
            return MotorCommand(forward=0.0, turn=0.0, stop=True)

        current_pose = high_rate_observation.pose
        desired_heading = self._active_intent.desired_heading_rad
        range_error = 99.0
        if target_pose is not None:
            delta_x = target_pose.x - current_pose.x
            delta_y = target_pose.y - current_pose.y
            desired_heading = math.atan2(delta_y, delta_x)
            range_error = math.hypot(delta_x, delta_y)
        assert desired_heading is not None
        heading_error = self._wrap_angle(desired_heading - current_pose.heading_rad)
        turn_command = self._clamp(
            heading_error * 0.9, limit=self._active_intent.constraints.max_turn_normalised
        )
        if range_error < 0.25:
            return MotorCommand(forward=0.0, turn=turn_command * 0.5)
        if self._active_intent.mode == IntentMode.FOLLOW:
            min_distance = self._active_intent.constraints.min_distance_body_lengths
            max_distance = self._active_intent.constraints.max_distance_body_lengths
            if min_distance is not None and range_error < (min_distance + 0.15):
                return MotorCommand(forward=-0.35, turn=turn_command)
            if (
                min_distance is not None
                and max_distance is not None
                and min_distance <= range_error <= max_distance
            ):
                return MotorCommand(forward=0.0, turn=turn_command * 0.6)
        heading_alignment = max(0.0, 1.0 - abs(heading_error))
        forward_target = max(0.2, min(0.65, self._active_intent.desired_speed or 0.5))
        forward_command = self._clamp(
            forward_target * heading_alignment,
            limit=self._active_intent.constraints.max_speed_normalised,
        )
        return MotorCommand(forward=forward_command, turn=turn_command)

    @staticmethod
    def _wrap_angle(angle_rad: float) -> float:
        return math.atan2(math.sin(angle_rad), math.cos(angle_rad))

    @staticmethod
    def _clamp(value: float, limit: float = 1.0) -> float:
        return max(-limit, min(limit, value))
