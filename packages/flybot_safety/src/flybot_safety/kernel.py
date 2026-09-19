from __future__ import annotations

from dataclasses import dataclass, field

from flybot_core.config import SafetyConfig
from flybot_core.models import HighRateObservation, MotorCommand


@dataclass
class SafetyOutcome:
    clamped_command: MotorCommand
    active_flags: list[str] = field(default_factory=list)


class SafetyKernel:
    def __init__(self, safety_config: SafetyConfig) -> None:
        self._safety_config = safety_config
        self._e_stop_latched = False

    def set_estop_latched(self, latched: bool) -> None:
        self._e_stop_latched = latched

    def reset(self) -> None:
        self._e_stop_latched = False

    def enforce(
        self, motor_command: MotorCommand, observation: HighRateObservation
    ) -> SafetyOutcome:
        active_flags: list[str] = []
        if self._e_stop_latched or motor_command.stop:
            active_flags.append("SAFETY_STOP")
            return SafetyOutcome(
                clamped_command=MotorCommand(forward=0.0, turn=0.0, stop=True),
                active_flags=active_flags,
            )

        clamped_forward = max(
            -self._safety_config.max_speed_normalised,
            min(self._safety_config.max_speed_normalised, motor_command.forward),
        )
        clamped_turn = max(
            -self._safety_config.max_turn_normalised,
            min(self._safety_config.max_turn_normalised, motor_command.turn),
        )
        if clamped_forward != motor_command.forward:
            active_flags.append("SPEED_CLAMP")
        if clamped_turn != motor_command.turn:
            active_flags.append("TURN_CLAMP")

        if not self._is_inside_geofence(observation.pose.x, observation.pose.y):
            active_flags.append("GEOFENCE_STOP")
            return SafetyOutcome(
                clamped_command=MotorCommand(forward=0.0, turn=0.0, stop=True),
                active_flags=active_flags,
            )

        if any(
            sample.force > self._safety_config.contact_force_limit
            for sample in observation.contacts
        ):
            active_flags.append("CONTACT_FORCE_STOP")
            return SafetyOutcome(
                clamped_command=MotorCommand(forward=0.0, turn=0.0, stop=True),
                active_flags=active_flags,
            )

        return SafetyOutcome(
            clamped_command=MotorCommand(forward=clamped_forward, turn=clamped_turn),
            active_flags=active_flags,
        )

    def _is_inside_geofence(self, position_x: float, position_y: float) -> bool:
        geofence = self._safety_config.geofence
        return (
            geofence.x_min <= position_x <= geofence.x_max
            and geofence.y_min <= position_y <= geofence.y_max
        )
