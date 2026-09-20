from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Protocol

from openai import OpenAI

from flybot_core.config import BrainConfig, EnvironmentSettings, OpenAIReasoningEffort
from flybot_core.ids import new_identifier
from flybot_core.models import (
    MissionPlan,
    OnFailurePolicy,
    PlanAction,
    PlanStep,
    TargetSpec,
    WorldState,
)

_DEFAULT_STEP_TIMEOUT_S = 30.0
_DEFAULT_ON_FAILURE_POLICY = OnFailurePolicy.REPLAN
_PLAN_ACTION_ALIASES = {
    "MOVE": PlanAction.NAVIGATE,
    "MOVE_TO": PlanAction.NAVIGATE,
    "GO": PlanAction.NAVIGATE,
    "GO_TO": PlanAction.NAVIGATE,
    "HALT": PlanAction.STOP,
}
_ON_FAILURE_ALIASES = {
    "ABORT": OnFailurePolicy.STOP,
    "CANCEL": OnFailurePolicy.STOP,
    "CONTINUE": OnFailurePolicy.SKIP,
}


class CognitiveProvider(Protocol):
    async def build_plan(self, instruction: str, world_state: WorldState) -> MissionPlan: ...


def _normalized_identifier(raw_identifier: object, prefix: str) -> str:
    if isinstance(raw_identifier, str):
        normalized_identifier = raw_identifier.strip()
        if normalized_identifier:
            return normalized_identifier
    return new_identifier(prefix)


def _normalized_string_list(raw_value: object) -> list[str]:
    if not isinstance(raw_value, list):
        return []
    return [value.strip() for value in raw_value if isinstance(value, str) and value.strip()]


def _coerced_plan_action(raw_action: object) -> PlanAction:
    if isinstance(raw_action, PlanAction):
        return raw_action
    if isinstance(raw_action, str):
        normalized_action = raw_action.strip().upper().replace("-", "_").replace(" ", "_")
        if normalized_action in _PLAN_ACTION_ALIASES:
            return _PLAN_ACTION_ALIASES[normalized_action]
        try:
            return PlanAction(normalized_action)
        except ValueError:
            return PlanAction.STOP
    return PlanAction.STOP


def _coerced_on_failure(raw_policy: object) -> OnFailurePolicy:
    if isinstance(raw_policy, OnFailurePolicy):
        return raw_policy
    if isinstance(raw_policy, str):
        normalized_policy = raw_policy.strip().upper().replace("-", "_").replace(" ", "_")
        if normalized_policy in _ON_FAILURE_ALIASES:
            return _ON_FAILURE_ALIASES[normalized_policy]
        try:
            return OnFailurePolicy(normalized_policy)
        except ValueError:
            return _DEFAULT_ON_FAILURE_POLICY
    return _DEFAULT_ON_FAILURE_POLICY


def _coerced_timeout_seconds(raw_timeout: object) -> float:
    if isinstance(raw_timeout, int | float):
        timeout_seconds = float(raw_timeout)
        if timeout_seconds > 0.0:
            return timeout_seconds
    return _DEFAULT_STEP_TIMEOUT_S


def _coerced_target_spec(raw_target: object) -> TargetSpec | None:
    if raw_target is None:
        return None
    if isinstance(raw_target, TargetSpec):
        return raw_target
    if isinstance(raw_target, str):
        normalized_label = raw_target.strip()
        if normalized_label:
            return TargetSpec(label=normalized_label)
        return None
    if isinstance(raw_target, dict):
        try:
            return TargetSpec.model_validate(raw_target)
        except Exception:
            return None
    return None


def normalize_mission_plan(
    raw_plan: object,
    fallback_objective: str = "Mission objective unavailable.",
) -> MissionPlan:
    if isinstance(raw_plan, MissionPlan):
        return raw_plan
    if not isinstance(raw_plan, dict):
        raw_plan = {}

    mission_identifier = _normalized_identifier(raw_plan.get("mission_id"), "mission")
    raw_objective = raw_plan.get("objective")
    objective = (
        raw_objective.strip()
        if isinstance(raw_objective, str) and raw_objective.strip()
        else fallback_objective
    )
    assumptions = _normalized_string_list(raw_plan.get("assumptions"))
    completion_summary_fields = _normalized_string_list(raw_plan.get("completion_summary_fields"))

    raw_steps = raw_plan.get("steps")
    normalized_steps: list[PlanStep] = []
    if isinstance(raw_steps, list):
        for raw_step in raw_steps:
            step_payload = raw_step if isinstance(raw_step, dict) else {}
            raw_parameters = step_payload.get("parameters")
            parameters = raw_parameters if isinstance(raw_parameters, dict) else {}
            normalized_steps.append(
                PlanStep(
                    step_id=_normalized_identifier(step_payload.get("step_id"), "step"),
                    action=_coerced_plan_action(step_payload.get("action")),
                    target=_coerced_target_spec(step_payload.get("target")),
                    parameters=parameters,
                    timeout_s=_coerced_timeout_seconds(step_payload.get("timeout_s")),
                    on_failure=_coerced_on_failure(step_payload.get("on_failure")),
                )
            )
    if not normalized_steps:
        normalized_steps.append(
            PlanStep(
                step_id=new_identifier("step"),
                action=PlanAction.STOP,
                target=None,
                parameters={},
                timeout_s=_DEFAULT_STEP_TIMEOUT_S,
                on_failure=OnFailurePolicy.STOP,
            )
        )
    return MissionPlan(
        mission_id=mission_identifier,
        objective=objective,
        assumptions=assumptions,
        steps=normalized_steps,
        completion_summary_fields=completion_summary_fields,
    )


@dataclass
class FakeBrainProvider:
    brain_config: BrainConfig

    async def build_plan(self, instruction: str, world_state: WorldState) -> MissionPlan:
        mission_identifier = new_identifier("mission")
        normalized_instruction = instruction.strip().lower()
        if "stop" in normalized_instruction:
            return MissionPlan(
                mission_id=mission_identifier,
                objective=instruction,
                assumptions=["Generated by FakeBrainProvider"],
                steps=[
                    PlanStep(
                        step_id=new_identifier("step"),
                        action=PlanAction.STOP,
                        target=None,
                        parameters={},
                        timeout_s=2.0,
                        on_failure=OnFailurePolicy.STOP,
                    )
                ],
                completion_summary_fields=["status"],
            )

        if "patrol" in normalized_instruction:
            return MissionPlan(
                mission_id=mission_identifier,
                objective=instruction,
                assumptions=["Generated by FakeBrainProvider for PATROL/OBSERVE demo behavior"],
                steps=[
                    PlanStep(
                        step_id=new_identifier("step"),
                        action=PlanAction.PATROL,
                        target=None,
                        parameters={
                            "waypoints": [[0.8, 0.0], [0.8, 0.8], [0.0, 0.8], [0.0, 0.0]],
                            "stand_off_body_lengths": 0.35,
                            "speed": 0.6,
                        },
                        timeout_s=14.0,
                        on_failure=OnFailurePolicy.REPLAN,
                    ),
                    PlanStep(
                        step_id=new_identifier("step"),
                        action=PlanAction.OBSERVE,
                        target=None,
                        parameters={"dwell_s": 3.0, "allow_recording": False},
                        timeout_s=8.0,
                        on_failure=OnFailurePolicy.REPLAN,
                    ),
                ],
                completion_summary_fields=["change_events_count", "recording_enabled"],
            )

        if "follow" in normalized_instruction:
            follow_target = self._resolve_target_spec("green target", world_state)
            return MissionPlan(
                mission_id=mission_identifier,
                objective=instruction,
                assumptions=["Generated by FakeBrainProvider for FOLLOW demo behavior"],
                steps=[
                    PlanStep(
                        step_id=new_identifier("step"),
                        action=PlanAction.FOLLOW,
                        target=follow_target,
                        parameters={
                            "speed": 0.45,
                            "duration_s": 8.0,
                            "min_distance_body_lengths": 1.35,
                            "max_distance_body_lengths": 1.8,
                        },
                        timeout_s=12.0,
                        on_failure=OnFailurePolicy.REPLAN,
                    )
                ],
                completion_summary_fields=["follow_min_distance_observed"],
            )

        if "touch" in normalized_instruction or "nudge" in normalized_instruction:
            touch_target = self._resolve_target_spec(normalized_instruction, world_state)
            touch_action = (
                PlanAction.NUDGE if "nudge" in normalized_instruction else PlanAction.TOUCH
            )
            return MissionPlan(
                mission_id=mission_identifier,
                objective=instruction,
                assumptions=["Generated by FakeBrainProvider for TOUCH/NUDGE demo behavior"],
                steps=[
                    PlanStep(
                        step_id=new_identifier("step"),
                        action=touch_action,
                        target=touch_target,
                        parameters={
                            "speed": 0.45,
                            "stand_off_body_lengths": 0.25,
                            "max_force": 0.35,
                            "retract_distance": 0.8,
                            "nudged_displacement": 0.3,
                        },
                        timeout_s=16.0,
                        on_failure=OnFailurePolicy.STOP,
                    )
                ],
                completion_summary_fields=["touch_contact_count", "touch_target_object_id"],
            )

        if "inspect" in normalized_instruction:
            inspect_target = self._resolve_target_spec(normalized_instruction, world_state)
            return MissionPlan(
                mission_id=mission_identifier,
                objective=instruction,
                assumptions=[
                    "Generated by FakeBrainProvider for INSPECT/RETURN_HOME demo behavior"
                ],
                steps=[
                    PlanStep(
                        step_id=new_identifier("step"),
                        action=PlanAction.INSPECT,
                        target=inspect_target,
                        parameters={
                            "viewpoints": 2,
                            "radius_body_lengths": 0.6,
                            "speed": 0.6,
                            "stand_off_body_lengths": 0.35,
                        },
                        timeout_s=24.0,
                        on_failure=OnFailurePolicy.REPLAN,
                    ),
                    PlanStep(
                        step_id=new_identifier("step"),
                        action=PlanAction.RETURN_HOME,
                        target=None,
                        parameters={"speed": 0.5, "stand_off_body_lengths": 0.7},
                        timeout_s=14.0,
                        on_failure=OnFailurePolicy.STOP,
                    ),
                ],
                completion_summary_fields=["result"],
            )

        resolved_target = self._resolve_target_spec(normalized_instruction, world_state)
        return MissionPlan(
            mission_id=mission_identifier,
            objective=instruction,
            assumptions=["Generated by FakeBrainProvider with deterministic parsing"],
            steps=[
                PlanStep(
                    step_id=new_identifier("step"),
                    action=PlanAction.NAVIGATE,
                    target=resolved_target,
                    parameters={"stand_off_body_lengths": 0.8, "speed": 0.5},
                    timeout_s=30.0,
                    on_failure=OnFailurePolicy.REPLAN,
                )
            ],
            completion_summary_fields=["target", "result"],
        )

    def _resolve_target_spec(self, instruction: str, world_state: WorldState) -> TargetSpec:
        color_pattern = r"(red|blue|green|yellow|white|gray)"
        shape_pattern = r"(cube|block|target|sphere|marker|object)"
        color_match = re.search(color_pattern, instruction)
        shape_match = re.search(shape_pattern, instruction)
        attributes: dict[str, str] = {}
        label: str | None = None
        if color_match is not None:
            attributes["color"] = color_match.group(1)
        if shape_match is not None:
            label = shape_match.group(1)

        for track in world_state.objects:
            if label is not None and track.label != label:
                continue
            if any(track.attributes.get(key) != value for key, value in attributes.items()):
                continue
            return TargetSpec(
                label=track.label, attributes=track.attributes, track_id=track.track_id
            )

        return TargetSpec(label=label or "target", attributes=attributes)


@dataclass
class OpenAICognitiveProvider:
    brain_config: BrainConfig
    openai_model: str
    openai_api_key: str
    openai_reasoning_effort: OpenAIReasoningEffort

    async def build_plan(self, instruction: str, world_state: WorldState) -> MissionPlan:
        client = OpenAI(api_key=self.openai_api_key)
        response = client.responses.create(
            model=self.openai_model,
            reasoning={"effort": self.openai_reasoning_effort},
            text={
                "format": {
                    "type": "json_schema",
                    "name": "mission_plan",
                    "schema": MissionPlan.model_json_schema(),
                    "strict": True,
                }
            },
            input=[
                {
                    "role": "system",
                    "content": (
                        "You are FlyBot CognitiveBrain. "
                        "Output only JSON matching MissionPlan fields: "
                        "mission_id, objective, assumptions, steps, completion_summary_fields. "
                        "Actions must be limited to NAVIGATE, SEARCH, PATROL, OBSERVE, "
                        "INSPECT, APPROACH, "
                        "FOLLOW, TOUCH, NUDGE, RETREAT, RETURN_HOME, WAIT, STOP. "
                        "Every PlanStep must include step_id, action, parameters, timeout_s, "
                        "on_failure. "
                        "Never emit motor/joint commands."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "instruction": instruction,
                            "world_state": world_state.model_dump(mode="json"),
                            "intent_ttl_ms": 1500,
                        }
                    ),
                },
            ],
            timeout=self.brain_config.request_timeout_s,
        )
        text_output = response.output_text
        if not isinstance(text_output, str) or not text_output.strip():
            raise RuntimeError("OpenAI Responses API returned an empty MissionPlan payload.")
        try:
            response_payload = json.loads(text_output)
        except json.JSONDecodeError as error:
            raise RuntimeError(
                "OpenAI Responses API returned invalid JSON for MissionPlan."
            ) from error
        return normalize_mission_plan(response_payload, fallback_objective=instruction)


def create_cognitive_provider(brain_config: BrainConfig) -> CognitiveProvider:
    environment_settings = EnvironmentSettings()
    if brain_config.provider.lower() == "openai":
        if environment_settings.openai_api_key is None:
            raise RuntimeError("OPENAI_API_KEY is required when BRAIN_PROVIDER=openai")
        return OpenAICognitiveProvider(
            brain_config=brain_config,
            openai_model=brain_config.model,
            openai_api_key=environment_settings.openai_api_key,
            openai_reasoning_effort=brain_config.reasoning_effort,
        )
    return FakeBrainProvider(brain_config=brain_config)
