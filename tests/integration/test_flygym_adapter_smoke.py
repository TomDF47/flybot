import asyncio

import pytest

from flybot_api.service import FlyBotRuntime


@pytest.mark.asyncio
async def test_flygym_backend_smoke_if_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BODY_BACKEND", "flygym")
    monkeypatch.setenv("SIMULATION_BACKEND", "flygym")
    monkeypatch.setenv("TEST_GROUND_TRUTH", "true")

    try:
        runtime = FlyBotRuntime()
    except RuntimeError as runtime_error:
        message = str(runtime_error).lower()
        if (
            "not installed" in message
            or "unavailable" in message
            or "headless" in message
            or "initialization failed" in message
        ):
            pytest.skip(f"FlyGym unavailable in this environment: {runtime_error}")
        raise

    try:
        await runtime.start()
    except RuntimeError as runtime_error:
        message = str(runtime_error).lower()
        if (
            "not installed" in message
            or "unavailable" in message
            or "headless" in message
            or "initialization failed" in message
        ):
            pytest.skip(f"FlyGym unavailable in this environment: {runtime_error}")
        raise

    try:
        await asyncio.sleep(0.05)
        state = runtime.status()
        assert state.backend == "flygym"
        assert "pose" in state.body_state
        assert state.frame.width > 0
        assert state.frame.height > 0
    finally:
        await runtime.stop()
