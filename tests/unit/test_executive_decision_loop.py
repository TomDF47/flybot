from flybot_core.config import FlyBotConfig
from flybot_core.models import (
    IntentMode,
    ObjectTrack,
    OnFailurePolicy,
    PlanAction,
    PlanStep,
    Pose2D,
    TargetSpec,
    WorldState,
)
from flybot_executive import MissionExecutive
from flybot_perception.resolver import ObjectTrackResolver


def _world_at_target() -> WorldState:
    pose = Pose2D(x=1.0, y=1.0, heading_rad=0.0)
    return WorldState(
        robot_pose=pose,
        objects=[
            ObjectTrack(
                track_id="cube_red",
                label="cube",
                attributes={"color": "red"},
                confidence=1.0,
                relative_range_body_lengths=0.0,
                world_pose=pose,
            )
        ],
    )


def _navigate_step() -> PlanStep:
    return PlanStep(
        step_id="step_navigate",
        action=PlanAction.NAVIGATE,
        target=TargetSpec(label="cube", attributes={"color": "red"}, track_id="cube_red"),
        parameters={"stand_off_body_lengths": 0.8, "speed": 0.5},
        timeout_s=30.0,
        on_failure=OnFailurePolicy.REPLAN,
    )


def _stop_step() -> PlanStep:
    return PlanStep(
        step_id="step_stop",
        action=PlanAction.STOP,
        target=None,
        parameters={},
        timeout_s=2.0,
        on_failure=OnFailurePolicy.STOP,
    )


def test_loop_holds_between_actions_and_finishes_on_stop() -> None:
    executive = MissionExecutive(
        config=FlyBotConfig(),
        resolver=ObjectTrackResolver(use_ground_truth_for_tests=True),
    )
    world_state = _world_at_target()
    executive.begin_decision_loop("mission_loop", "Walk to the red cube.")
    assert executive.needs_decision()

    hold_intent = executive.tick(world_state, [])
    assert hold_intent is not None
    assert hold_intent.mode == IntentMode.WAIT

    assert executive.enqueue_bounded_action(_navigate_step(), world_state)
    assert executive.needs_decision() is False
    assert executive.enqueue_bounded_action(_stop_step(), world_state) is False

    after_navigate = executive.tick(world_state, [])
    assert after_navigate is not None
    assert after_navigate.mode == IntentMode.WAIT
    assert executive.needs_decision()
    assert len(executive.current_state().completed_steps) == 1

    assert executive.enqueue_bounded_action(_stop_step(), world_state)
    finished_intent = executive.tick(world_state, [])
    assert finished_intent is not None
    assert finished_intent.mode == IntentMode.STOP
    assert executive.mission_loop_finished()
    timeline_events = [event["event"] for event in executive.current_state().timeline]
    assert "AWAITING_DECISION" in timeline_events
    assert "MISSION_COMPLETED" in timeline_events
