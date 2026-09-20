import pytest

from flybot_brain.providers import OpenAICognitiveProvider
from flybot_core.config import BrainConfig, load_config
from flybot_core.models import WorldState


def test_load_config_parses_openai_model_and_reasoning_effort_separately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5")
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "high")
    monkeypatch.setenv("BRAIN_PROVIDER", "openai")

    loaded_config = load_config("configs/default.yaml")

    assert loaded_config.brain.provider == "openai"
    assert loaded_config.brain.model == "gpt-5"
    assert loaded_config.brain.reasoning_effort == "high"


@pytest.mark.asyncio
async def test_openai_provider_sends_reasoning_effort_as_separate_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_request: dict[str, object] = {}

    class FakeResponsesClient:
        def create(self, **kwargs: object) -> object:
            captured_request.update(kwargs)
            return type(
                "FakeResponse",
                (),
                {
                    "output_text": (
                        '{"mission_id":"mission_test","objective":"test","steps":[{"step_id":"step_1",'
                        '"action":"STOP","timeout_s":2.0}],"completion_summary_fields":[]}'
                    )
                },
            )()

    class FakeOpenAIClient:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key
            self.responses = FakeResponsesClient()

    monkeypatch.setattr("flybot_brain.providers.OpenAI", FakeOpenAIClient)

    provider = OpenAICognitiveProvider(
        brain_config=BrainConfig(provider="openai", model="gpt-5", reasoning_effort="minimal"),
        openai_model="gpt-5",
        openai_api_key="test-api-key",
        openai_reasoning_effort="minimal",
    )

    plan = await provider.build_plan(
        instruction="Stop now.",
        world_state=WorldState(),
    )

    assert plan.steps[0].action.value == "STOP"
    assert captured_request["model"] == "gpt-5"
    assert captured_request["reasoning"] == {"effort": "minimal"}
