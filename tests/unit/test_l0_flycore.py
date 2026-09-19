from flybot_core.models import (
    HighRateObservation,
    IntentCommand,
    IntentMode,
    MotionConstraints,
    Pose2D,
)
from flybot_flycore import L0FlyCoreController


def test_expired_intent_results_in_stop_command() -> None:
    flycore_controller = L0FlyCoreController()
    intent = IntentCommand(
        intent_id="intent_1",
        mission_id="mission_1",
        step_id="step_1",
        mode=IntentMode.FORWARD,
        desired_speed=0.5,
        constraints=MotionConstraints(),
        ttl_ms=1,
        issued_at_ns=0,
    )
    flycore_controller.set_intent(intent)
    command = flycore_controller.tick(
        HighRateObservation(
            pose=Pose2D(x=0.0, y=0.0, heading_rad=0.0),
            contacts=[],
            timestamp_ns=5_000_000,
        ),
        dt_s=0.01,
    )
    assert command.stop is True


def test_navigate_moves_forward_when_facing_target() -> None:
    flycore_controller = L0FlyCoreController()
    intent = IntentCommand(
        intent_id="intent_2",
        mission_id="mission_2",
        step_id="step_2",
        mode=IntentMode.NAVIGATE,
        target_pose=Pose2D(x=2.0, y=0.0, heading_rad=0.0),
        desired_speed=0.6,
        constraints=MotionConstraints(),
        ttl_ms=10_000,
    )
    flycore_controller.set_intent(intent)
    command = flycore_controller.tick(
        HighRateObservation(
            pose=Pose2D(x=0.0, y=0.0, heading_rad=0.0),
            contacts=[],
            timestamp_ns=intent.issued_at_ns + 1_000,
        ),
        dt_s=0.01,
    )
    assert command.forward > 0.0
