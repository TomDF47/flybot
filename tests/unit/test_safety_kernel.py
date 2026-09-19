from flybot_core.config import SafetyConfig
from flybot_core.models import ContactSample, HighRateObservation, MotorCommand, Pose2D
from flybot_safety import SafetyKernel


def build_observation(position_x: float = 0.0, position_y: float = 0.0) -> HighRateObservation:
    return HighRateObservation(
        pose=Pose2D(x=position_x, y=position_y, heading_rad=0.0),
        contacts=[],
        timestamp_ns=123,
    )


def test_speed_and_turn_are_clamped() -> None:
    safety_kernel = SafetyKernel(SafetyConfig())
    outcome = safety_kernel.enforce(MotorCommand(forward=0.9, turn=0.9), build_observation())
    assert outcome.clamped_command.forward == 0.65
    assert outcome.clamped_command.turn == 0.75
    assert "SPEED_CLAMP" in outcome.active_flags
    assert "TURN_CLAMP" in outcome.active_flags


def test_geofence_violation_stops_motion() -> None:
    safety_kernel = SafetyKernel(SafetyConfig())
    outcome = safety_kernel.enforce(
        MotorCommand(forward=0.2, turn=0.0), build_observation(position_x=10.0)
    )
    assert outcome.clamped_command.stop is True
    assert "GEOFENCE_STOP" in outcome.active_flags


def test_contact_force_limit_stops_motion() -> None:
    safety_kernel = SafetyKernel(SafetyConfig(contact_force_limit=0.2))
    observation = HighRateObservation(
        pose=Pose2D(x=0.0, y=0.0, heading_rad=0.0),
        contacts=[ContactSample(object_id="target", force=0.25)],
        timestamp_ns=123,
    )
    outcome = safety_kernel.enforce(MotorCommand(forward=0.2, turn=0.0), observation)
    assert outcome.clamped_command.stop is True
    assert "CONTACT_FORCE_STOP" in outcome.active_flags
