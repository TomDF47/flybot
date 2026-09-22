import pytest

from flybot_api.service import FlyBotRuntime
from tests.integration.runtime_test_utils import (
    mission_timeout_from_plan,
    wait_for_mission_terminal_state,
)


@pytest.mark.asyncio
async def test_runtime_openai_plan_normalization_handles_missing_step_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRAIN_PROVIDER", "openai")
    monkeypatch.setenv("JEV_ENABLED", "false")
    monkeypatch.setenv("BODY_BACKEND", "mock")
    monkeypatch.setenv("SIMULATION_BACKEND", "mock")
    monkeypatch.setenv("SIMULATION_SEED", "42")
    monkeypatch.setenv("OPENAI_API_KEY", "test-api-key")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5")
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "xhigh")
    monkeypatch.setenv("TEST_GROUND_TRUTH", "true")
    monkeypatch.setenv("ALLOW_GROUND_TRUTH_FOR_TESTS", "true")

    class FakeResponsesClient:
        def create(self, **kwargs: object) -> object:  # noqa: ARG002
            return type(
                "FakeResponse",
                (),
                {
                    "output_text": (
                        '{"objective":"Stop now.","steps":[{"action":"stop","parameters":{}}]}'
                    )
                },
            )()

    class FakeOpenAIClient:
        def __init__(self, api_key: str) -> None:  # noqa: ARG002
            self.responses = FakeResponsesClient()

    monkeypatch.setattr("flybot_brain.providers.OpenAI", FakeOpenAIClient)

    runtime = FlyBotRuntime()
    await runtime.start()
    try:
        mission_id = await runtime.submit_instruction("Stop now.")
        current_plan = runtime.executive.current_state().plan
        assert current_plan is not None
        assert current_plan.mission_id.startswith("mission_")
        assert current_plan.steps[0].step_id.startswith("step_")

        mission_state = await wait_for_mission_terminal_state(
            runtime,
            mission_id,
            timeout_s=mission_timeout_from_plan(runtime, mission_id),
        )
        assert mission_state["failed_step"] is None
        assert mission_state["completed_steps"]
    finally:
        await runtime.stop()
