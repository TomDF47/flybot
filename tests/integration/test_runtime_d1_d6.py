import asyncio
from time import monotonic

import pytest

from flybot_api.service import FlyBotRuntime


@pytest.mark.asyncio
async def test_d1_walk_to_red_cube_completes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_GROUND_TRUTH", "true")
    runtime = FlyBotRuntime()
    await runtime.start()
    try:
        mission_id = await runtime.submit_instruction("Walk to the red cube.")
        timeout_s = 10.0
        started_at = monotonic()
        while monotonic() - started_at < timeout_s:
            mission_state = runtime.status().mission_state
            if mission_state["mission_id"] == mission_id and mission_state["completed_steps"]:
                assert mission_state["failed_step"] is None
                return
            await asyncio.sleep(0.05)
        pytest.fail("D1 mission did not complete in time")
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_d6_estop_calls_safe_stop_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_GROUND_TRUTH", "true")
    runtime = FlyBotRuntime()
    await runtime.start()
    try:
        await runtime.e_stop()
        await asyncio.sleep(0.05)
        state = runtime.status()
        assert state.body_state["e_stop_latched"] is True
    finally:
        await runtime.stop()
