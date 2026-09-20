import math

import pytest

from flybot_api.service import FlyBotRuntime
from flybot_body_flygym.adapter import MockBodyAdapter
from flybot_core.models import PlanAction
from tests.integration.runtime_test_utils import (
    configure_deterministic_runtime_env,
    mission_timeout_from_plan,
    wait_for_failed_step_reason,
    wait_for_mission_terminal_state,
)


@pytest.mark.asyncio
async def test_d2_patrol_observe_detects_change(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_deterministic_runtime_env(monkeypatch)
    runtime = FlyBotRuntime()
    await runtime.start()
    try:
        mission_id = await runtime.submit_instruction(
            "Patrol the arena and tell me if anything changes."
        )
        mission_state = await wait_for_mission_terminal_state(
            runtime,
            mission_id,
            timeout_s=mission_timeout_from_plan(runtime, mission_id),
        )
        assert mission_state["failed_step"] is None
        current_plan = runtime.executive.current_state().plan
        assert current_plan is not None
        plan_actions = [step.action for step in current_plan.steps]
        assert plan_actions[0] == PlanAction.PATROL
        assert PlanAction.OBSERVE in plan_actions
        change_events_count = mission_state["change_events_count"]
        assert isinstance(change_events_count, int | float)
        assert change_events_count >= 1
        assert mission_state["recording_enabled"] is False
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_d4_follow_respects_min_distance(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_deterministic_runtime_env(monkeypatch)
    runtime = FlyBotRuntime()
    await runtime.start()
    try:
        mission_id = await runtime.submit_instruction(
            "Follow the moving green target but do not get closer than one body length."
        )
        mission_state = await wait_for_mission_terminal_state(
            runtime,
            mission_id,
            timeout_s=mission_timeout_from_plan(runtime, mission_id),
        )
        assert mission_state["failed_step"] is None
        current_plan = runtime.executive.current_state().plan
        assert current_plan is not None
        assert any(step.action == PlanAction.FOLLOW for step in current_plan.steps)
        min_follow_distance = mission_state["follow_min_distance_observed"]
        assert isinstance(min_follow_distance, int | float)
        # Control loop timing can introduce very small jitter around the 1.0 boundary.
        assert min_follow_distance >= 0.99
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_d4_follow_target_lost_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_deterministic_runtime_env(monkeypatch)
    runtime = FlyBotRuntime()
    await runtime.start()
    try:
        body_adapter = runtime.body_adapter
        assert isinstance(body_adapter, MockBodyAdapter)
        body_adapter._objects = [
            arena_object
            for arena_object in body_adapter._objects
            if arena_object.object_id != "target_green_sphere"
        ]
        mission_id = await runtime.submit_instruction(
            "Follow the moving green target but do not get closer than one body length."
        )
        mission_state = await wait_for_failed_step_reason(
            runtime,
            mission_id,
            expected_reason="TARGET_LOST",
            timeout_s=10.0,
        )
        assert mission_state["failed_step"] is not None
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_d3_touch_blue_block_once(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_deterministic_runtime_env(monkeypatch)
    runtime = FlyBotRuntime()
    await runtime.start()
    try:
        mission_id = await runtime.submit_instruction(
            "Find the blue block and touch it once with your right front leg."
        )
        mission_state = await wait_for_mission_terminal_state(
            runtime,
            mission_id,
            timeout_s=mission_timeout_from_plan(runtime, mission_id),
        )
        assert mission_state["failed_step"] is None
        current_plan = runtime.executive.current_state().plan
        assert current_plan is not None
        assert any(step.action == PlanAction.TOUCH for step in current_plan.steps)
        assert mission_state["touch_contact_count"] == 1
        assert mission_state["touch_target_object_id"] == "target_blue_block"
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_d5_inspect_then_return_home(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_deterministic_runtime_env(monkeypatch)
    runtime = FlyBotRuntime()
    await runtime.start()
    try:
        mission_id = await runtime.submit_instruction(
            "Inspect the yellow object, circle it, then return home."
        )
        mission_state = await wait_for_mission_terminal_state(
            runtime,
            mission_id,
            timeout_s=mission_timeout_from_plan(runtime, mission_id),
        )
        assert mission_state["failed_step"] is None
        current_plan = runtime.executive.current_state().plan
        assert current_plan is not None
        plan_actions = [step.action for step in current_plan.steps]
        assert PlanAction.INSPECT in plan_actions
        assert plan_actions[-1] == PlanAction.RETURN_HOME
        current_state = runtime.status()
        final_pose = current_state.body_state["pose"]
        home_pose = current_state.world_state.home_pose
        distance_to_home = math.hypot(final_pose["x"] - home_pose.x, final_pose["y"] - home_pose.y)
        assert distance_to_home <= 0.8
    finally:
        await runtime.stop()
