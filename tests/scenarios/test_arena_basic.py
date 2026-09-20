from pathlib import Path

import pytest
import yaml
from tests.integration.runtime_test_utils import (
    configure_deterministic_runtime_env,
    mission_timeout_from_plan,
    wait_for_mission_terminal_state,
)

from flybot_api.service import FlyBotRuntime

SCENARIO_DATA = yaml.safe_load(Path("scenarios/arena_basic.yaml").read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_arena_basic_scenario_seeded(monkeypatch: pytest.MonkeyPatch) -> None:
    scenario_seed = int(SCENARIO_DATA["seed"])
    configure_deterministic_runtime_env(monkeypatch, simulation_seed=scenario_seed)
    runtime = FlyBotRuntime()
    await runtime.start()
    try:
        mission_id = await runtime.submit_instruction(str(SCENARIO_DATA["goals"][0]))
        mission_state = await wait_for_mission_terminal_state(
            runtime,
            mission_id,
            timeout_s=mission_timeout_from_plan(runtime, mission_id),
        )
        assert mission_state["failed_step"] is None
        assert mission_state["completed_steps"]
    finally:
        await runtime.stop()
