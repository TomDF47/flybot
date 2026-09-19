import asyncio
from pathlib import Path
from time import monotonic

import pytest
import yaml

from flybot_api.service import FlyBotRuntime

SCENARIO_DATA = yaml.safe_load(Path("scenarios/arena_basic.yaml").read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_arena_basic_scenario_seeded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_GROUND_TRUTH", "true")
    runtime = FlyBotRuntime()
    await runtime.reset(seed=int(SCENARIO_DATA["seed"]))
    await runtime.start()
    try:
        mission_id = await runtime.submit_instruction(str(SCENARIO_DATA["goals"][0]))
        started_at = monotonic()
        while monotonic() - started_at < 10.0:
            mission_state = runtime.status().mission_state
            if mission_state["mission_id"] == mission_id and mission_state["completed_steps"]:
                return
            await asyncio.sleep(0.05)
        pytest.fail("Scenario did not reach completion under seed")
    finally:
        await runtime.stop()
