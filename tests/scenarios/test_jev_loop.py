import asyncio
from time import monotonic
from typing import Any

import pytest
from tests.integration.runtime_test_utils import (
    configure_deterministic_runtime_env,
    wait_for_estop_latched,
)

from flybot_api.service import FlyBotRuntime
from flybot_brain.jev import JEV_RUNNING_STATUS, BoundedActionOption, JevChoice


@pytest.mark.asyncio
async def test_jev_loop_walks_to_the_red_cube_until_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_deterministic_runtime_env(monkeypatch, simulation_seed=42)
    monkeypatch.setenv("JEV_ENABLED", "true")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)

    runtime = FlyBotRuntime()
    await runtime.start()
    try:
        mission_id = await runtime.submit_instruction("Walk to the red cube.")
        decision_loop = runtime.status().mission_state["decision_loop"]
        assert isinstance(decision_loop, dict)
        assert decision_loop["enabled"] is True
        assert decision_loop["phase"] == "running"
        assert decision_loop["status_text"] == JEV_RUNNING_STATUS
        assert decision_loop["model"] == "typesafe/jev-1.13"
        assert decision_loop["source"] == "offline"

        terminal_state = await _wait_for_phase(runtime, mission_id, "done", timeout_s=20.0)
        assert terminal_state["failed_step"] is None
        timeline = terminal_state["timeline"]
        assert isinstance(timeline, list)
        completed_actions = [
            event["payload"]["action"]
            for event in timeline
            if isinstance(event, dict)
            and event.get("event") == "STEP_COMPLETED"
            and isinstance(event.get("payload"), dict)
        ]
        assert completed_actions == ["NAVIGATE", "STOP"]
        decision_actions = [
            event["payload"]["action"]
            for event in runtime.recorder.events
            if event["event"] == "JEV_DECISION"
        ]
        assert decision_actions == ["NAVIGATE", "STOP"]
        done_events = [
            event for event in runtime.recorder.events if event["event"] == "JEV_LOOP_DONE"
        ]
        done_event = done_events[-1]
        assert done_event["payload"]["reason"] == "completed"
        serialized_events = str(runtime.recorder.events)
        assert "torque" not in serialized_events
        assert "api_key" not in serialized_events.lower()
        final_loop = terminal_state["decision_loop"]
        assert isinstance(final_loop, dict)
        assert final_loop["status_text"] == "done"
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_estop_does_not_wait_for_jev(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_deterministic_runtime_env(monkeypatch, simulation_seed=42)
    monkeypatch.setenv("JEV_ENABLED", "true")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)

    runtime = FlyBotRuntime()

    class HangingJevClient:
        source = "offline"
        model = "typesafe/jev-1.13"

        async def choose(
            self,
            observation: dict[str, object],
            options: list[BoundedActionOption],
        ) -> JevChoice:
            del observation, options
            await asyncio.sleep(30)
            raise AssertionError("JEV choose should be cancelled by runtime shutdown")

    runtime._jev_client = HangingJevClient()
    await runtime.start()
    try:
        await runtime.submit_instruction("Walk to the red cube.")
        await asyncio.sleep(0.05)
        started_at_s = monotonic()
        await runtime.e_stop()
        assert monotonic() - started_at_s < 1.0
        await wait_for_estop_latched(runtime)
        decision_loop = runtime.status().mission_state["decision_loop"]
        assert isinstance(decision_loop, dict)
        assert decision_loop["phase"] == "failed"
    finally:
        await runtime.stop()


async def _wait_for_phase(
    runtime: FlyBotRuntime,
    mission_id: str,
    phase: str,
    *,
    timeout_s: float,
) -> dict[str, Any]:
    started_at_s = monotonic()
    last_state: dict[str, Any] = {}
    while monotonic() - started_at_s < timeout_s:
        mission_state = runtime.status().mission_state
        if mission_state["mission_id"] != mission_id:
            await asyncio.sleep(0.05)
            continue
        last_state = mission_state
        decision_loop = mission_state["decision_loop"]
        if isinstance(decision_loop, dict) and decision_loop.get("phase") == phase:
            return mission_state
        if isinstance(decision_loop, dict) and decision_loop.get("phase") == "failed":
            pytest.fail(f"JEV loop failed before {phase}: {mission_state}")
        await asyncio.sleep(0.05)
    pytest.fail(f"JEV loop did not reach {phase} in {timeout_s:.1f}s; last_state={last_state}")
