import pytest
from pydantic import ValidationError

from flybot_core.models import IntentCommand, MissionPlan, PlanAction, PlanStep, WorldState


def test_world_state_and_plan_step_have_safe_defaults() -> None:
    world_state = WorldState()
    assert world_state.robot_pose is None
    assert world_state.home_pose.x == 0.0
    assert world_state.objects == []

    wait_step = PlanStep(step_id="step_wait", action=PlanAction.WAIT)
    assert wait_step.target is None
    assert wait_step.timeout_s == 30.0


def test_mission_plan_still_requires_core_fields() -> None:
    with pytest.raises(ValidationError):
        MissionPlan.model_validate({"mission_id": "mission_only"})


def test_intent_command_still_requires_mission_identity() -> None:
    with pytest.raises(ValidationError):
        IntentCommand.model_validate({"step_id": "step_1", "mode": "STOP"})
