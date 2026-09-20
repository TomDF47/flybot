import pytest

from flybot_api.service import FlyBotRuntime
from tests.integration.runtime_test_utils import (
    configure_deterministic_runtime_env,
    mission_timeout_from_plan,
    wait_for_estop_latched,
    wait_for_mission_terminal_state,
)


@pytest.mark.asyncio
async def test_d1_walk_to_red_cube_completes(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_deterministic_runtime_env(monkeypatch)
    runtime = FlyBotRuntime()
    await runtime.start()
    try:
        mission_id = await runtime.submit_instruction("Walk to the red cube.")
        mission_state = await wait_for_mission_terminal_state(
            runtime,
            mission_id,
            timeout_s=mission_timeout_from_plan(runtime, mission_id),
        )
        assert mission_state["failed_step"] is None
        assert mission_state["completed_steps"]
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_d6_estop_calls_safe_stop_path(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_deterministic_runtime_env(monkeypatch)
    runtime = FlyBotRuntime()
    await runtime.start()
    try:
        await runtime.e_stop()
        await wait_for_estop_latched(runtime)
        state = runtime.status()
        assert state.body_state["e_stop_latched"] is True
    finally:
        await runtime.stop()
