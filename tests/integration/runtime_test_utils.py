from __future__ import annotations

import asyncio
from time import monotonic
from typing import Any

import pytest

from flybot_api.service import FlyBotRuntime

DEFAULT_POLL_INTERVAL_S = 0.05
MINIMUM_MISSION_TIMEOUT_S = 15.0
MISSION_TIMEOUT_BUFFER_S = 6.0
E_STOP_TIMEOUT_S = 2.0


def configure_deterministic_runtime_env(
    monkeypatch: pytest.MonkeyPatch, *, simulation_seed: int = 42
) -> None:
    """Pin runtime integration tests to deterministic fake/mock execution."""
    monkeypatch.setenv("BRAIN_PROVIDER", "fake")
    monkeypatch.setenv("JEV_ENABLED", "false")
    monkeypatch.setenv("BODY_BACKEND", "mock")
    monkeypatch.setenv("SIMULATION_BACKEND", "mock")
    monkeypatch.setenv("SIMULATION_SEED", str(simulation_seed))
    monkeypatch.setenv("TEST_GROUND_TRUTH", "true")
    monkeypatch.setenv("ALLOW_GROUND_TRUTH_FOR_TESTS", "true")
    monkeypatch.setenv("SESSION_RECORDING", "false")
    monkeypatch.setenv("RECORD_IMAGES_BY_DEFAULT", "false")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def mission_timeout_from_plan(
    runtime: FlyBotRuntime,
    mission_id: str,
    *,
    minimum_timeout_s: float = MINIMUM_MISSION_TIMEOUT_S,
    timeout_buffer_s: float = MISSION_TIMEOUT_BUFFER_S,
) -> float:
    """Derive timeout from configured step timeouts with a small grace buffer."""
    current_plan = runtime.executive.current_state().plan
    if current_plan is None or current_plan.mission_id != mission_id:
        return minimum_timeout_s
    planned_timeout_s = sum(max(0.0, step.timeout_s) for step in current_plan.steps)
    return max(minimum_timeout_s, planned_timeout_s + timeout_buffer_s)


async def wait_for_mission_terminal_state(
    runtime: FlyBotRuntime,
    mission_id: str,
    *,
    timeout_s: float,
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
) -> dict[str, Any]:
    """Wait until mission completes all steps or enters a failed step."""
    started_at_s = monotonic()
    last_mission_state = runtime.status().mission_state
    while monotonic() - started_at_s < timeout_s:
        mission_state = runtime.status().mission_state
        if mission_state["mission_id"] != mission_id:
            await asyncio.sleep(poll_interval_s)
            continue
        last_mission_state = mission_state
        if mission_state["failed_step"] is not None:
            return mission_state
        current_plan = runtime.executive.current_state().plan
        if (
            current_plan is not None
            and mission_state["active_step_index"] >= len(current_plan.steps)
        ):
            return mission_state
        await asyncio.sleep(poll_interval_s)

    timeline_tail = list(last_mission_state.get("timeline", []))[-5:]
    pytest.fail(
        "Mission "
        f"{mission_id} did not reach terminal state in {timeout_s:.1f}s; "
        f"last_state={last_mission_state}, timeline_tail={timeline_tail}"
    )


async def wait_for_failed_step_reason(
    runtime: FlyBotRuntime,
    mission_id: str,
    *,
    expected_reason: str,
    timeout_s: float,
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
) -> dict[str, Any]:
    started_at_s = monotonic()
    while monotonic() - started_at_s < timeout_s:
        mission_state = runtime.status().mission_state
        if mission_state["mission_id"] != mission_id:
            await asyncio.sleep(poll_interval_s)
            continue
        failed_step = mission_state["failed_step"]
        if isinstance(failed_step, dict) and failed_step.get("reason") == expected_reason:
            return mission_state
        await asyncio.sleep(poll_interval_s)

    pytest.fail(
        "Mission "
        f"{mission_id} did not fail with {expected_reason} in {timeout_s:.1f}s; "
        f"last_state={runtime.status().mission_state}"
    )


async def wait_for_estop_latched(
    runtime: FlyBotRuntime,
    *,
    timeout_s: float = E_STOP_TIMEOUT_S,
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
) -> None:
    started_at_s = monotonic()
    while monotonic() - started_at_s < timeout_s:
        if runtime.status().body_state.get("e_stop_latched") is True:
            return
        await asyncio.sleep(poll_interval_s)
    pytest.fail(f"E-stop latch was not observed within {timeout_s:.1f}s")
