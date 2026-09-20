from typing import Any

import pytest

from flybot_brain.providers import OpenAICognitiveProvider, normalize_mission_plan
from flybot_core.config import BrainConfig, load_config
from flybot_core.models import OnFailurePolicy, PlanAction, WorldState


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


def test_load_config_normalizes_legacy_reasoning_effort_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "max")
    monkeypatch.setenv("BRAIN_PROVIDER", "openai")

    loaded_config = load_config("configs/default.yaml")

    assert loaded_config.brain.reasoning_effort == "xhigh"


def test_load_config_rejects_unknown_reasoning_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "ultra")

    with pytest.raises(ValueError, match="OPENAI_REASONING_EFFORT"):
        load_config("configs/default.yaml")


def test_normalize_mission_plan_populates_missing_ids_and_step_defaults() -> None:
    normalized_plan = normalize_mission_plan(
        {
            "objective": "Stop now.",
            "steps": [
                {
                    "action": "stop",
                }
            ],
        }
    )

    assert normalized_plan.mission_id.startswith("mission_")
    assert normalized_plan.steps[0].step_id.startswith("step_")
    assert normalized_plan.steps[0].action == PlanAction.STOP
    assert normalized_plan.steps[0].timeout_s == 30.0
    assert normalized_plan.steps[0].on_failure == OnFailurePolicy.REPLAN
    assert normalized_plan.steps[0].parameters == {}


@pytest.mark.asyncio
async def test_openai_provider_sends_reasoning_effort_as_separate_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_request: dict[str, Any] = {}

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
    assert captured_request["text"]["format"]["type"] == "json_schema"
    assert captured_request["text"]["format"]["name"] == "mission_plan"
    schema = captured_request["text"]["format"]["schema"]
    assert "required" in schema
    assert "mission_id" in schema["required"]
    schema_definitions = schema.get("$defs", schema.get("definitions", {}))
    assert "step_id" in schema_definitions["PlanStep"]["required"]
    system_prompt = captured_request["input"][0]["content"]
    required_step_fields_message = (
        "Every PlanStep must include step_id, action, parameters, timeout_s, on_failure."
    )
    assert required_step_fields_message in system_prompt


@pytest.mark.asyncio
async def test_openai_provider_normalizes_missing_step_identifiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResponsesClient:
        def create(self, **kwargs: object) -> object:  # noqa: ARG002
            return type(
                "FakeResponse",
                (),
                {
                    "output_text": (
                        '{"objective":"Walk to the red cube.","steps":[{"action":"navigate",'
                        '"parameters":{"speed":0.4}}]}'
                    )
                },
            )()

    class FakeOpenAIClient:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key
            self.responses = FakeResponsesClient()

    monkeypatch.setattr("flybot_brain.providers.OpenAI", FakeOpenAIClient)

    provider = OpenAICognitiveProvider(
        brain_config=BrainConfig(provider="openai", model="gpt-5", reasoning_effort="xhigh"),
        openai_model="gpt-5",
        openai_api_key="test-api-key",
        openai_reasoning_effort="xhigh",
    )
    normalized_plan = await provider.build_plan(
        instruction="Walk to the red cube.",
        world_state=WorldState(),
    )

    assert normalized_plan.mission_id.startswith("mission_")
    assert normalized_plan.steps[0].step_id.startswith("step_")
    assert normalized_plan.steps[0].action == PlanAction.NAVIGATE
