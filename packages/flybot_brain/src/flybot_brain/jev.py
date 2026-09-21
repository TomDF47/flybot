from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic
from typing import Protocol

import httpx

from flybot_core.ids import new_identifier
from flybot_core.models import OnFailurePolicy, PlanAction, PlanStep, Pose2D, WorldState

JEV_DEFAULT_MODEL = "typesafe/jev-1.13"
JEV_RUNNING_STATUS = "Running in a loop until you are done"
JEV_MAX_CHOICES = 20
JEV_MAX_ATTEMPTS = 3
JEV_FAILURE_COOLDOWN_S = 4.0
JEV_STALE_RETRY_S = 0.25
_HAZARDOUS_SAFETY_FLAGS = frozenset({"GEOFENCE_STOP", "CONTACT_FORCE_STOP", "SAFETY_STOP"})
_HAZARD_ACTIONS = frozenset({PlanAction.RETREAT, PlanAction.STOP, PlanAction.WAIT})
_TRANSIENT_STATUS_CODES = frozenset({429, 500, 502, 503, 504, 529})
_OPENROUTER_DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
_TYPESAFE_DECISIONS_URL = "https://api.typesafe.ai/v1/systemone"
JEV_ACTION_INSTRUCTIONS = (
    "Choose one offered FlyBot action that best advances the current objective. "
    "Each choice is one bounded intent. Do not invent joint targets, torques, or "
    "raw motor commands. Prefer the next unfinished action. Choose STOP when the "
    "objective is already satisfied or no safe action remains. Active safety flags "
    "outrank the objective."
)


class JevDecisionError(Exception):
    """The decision response cannot be executed."""


class JevTransientError(Exception):
    """A retryable transport or service failure."""


class JevSelectionFailed(Exception):
    """The attempt budget ended without a fresh legal choice."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class BoundedActionOption:
    choice_id: str
    description: str
    step: PlanStep


@dataclass(frozen=True)
class JevChoice:
    choice_id: str
    source: str
    model: str
    latency_ms: int


class JevClient(Protocol):
    source: str
    model: str

    async def choose(
        self,
        observation: dict[str, object],
        options: list[BoundedActionOption],
    ) -> JevChoice: ...


def action_key(step: PlanStep) -> str:
    target_label = ""
    if step.target is not None:
        target_label = step.target.track_id or step.target.label or ""
    return f"{step.action.value}:{target_label}"


def describe_bounded_action(step: PlanStep) -> str:
    if step.action == PlanAction.STOP:
        return (
            "STOP and finish the mission. Choose this when the objective is complete "
            "or no safe action remains."
        )
    details = [step.action.value]
    if step.target is not None:
        if step.target.label:
            details.append(f"label={step.target.label}")
        if step.target.track_id:
            details.append(f"track={step.target.track_id}")
        for attribute_name, attribute_value in step.target.attributes.items():
            details.append(f"{attribute_name}={attribute_value}")
    return " ".join(details)


def _stop_step() -> PlanStep:
    return PlanStep(
        step_id=new_identifier("step"),
        action=PlanAction.STOP,
        target=None,
        parameters={},
        timeout_s=2.0,
        on_failure=OnFailurePolicy.STOP,
    )


def _retreat_step() -> PlanStep:
    return PlanStep(
        step_id=new_identifier("step"),
        action=PlanAction.RETREAT,
        target=None,
        parameters={"speed": 0.4},
        timeout_s=4.0,
        on_failure=OnFailurePolicy.STOP,
    )


def build_action_options(
    remaining_steps: list[PlanStep],
    *,
    cooled_down_keys: set[str],
    safety_flags: list[str],
) -> list[BoundedActionOption]:
    """Offer at most 20 bounded intents. STOP is always available."""
    hazardous = any(flag in _HAZARDOUS_SAFETY_FLAGS for flag in safety_flags)
    selected_steps: list[PlanStep] = []
    for step in remaining_steps:
        if action_key(step) in cooled_down_keys:
            continue
        if hazardous and step.action not in _HAZARD_ACTIONS:
            continue
        selected_steps.append(step)

    if hazardous and not any(step.action == PlanAction.RETREAT for step in selected_steps):
        selected_steps.insert(0, _retreat_step())

    if not any(step.action == PlanAction.STOP for step in selected_steps):
        selected_steps.append(_stop_step())

    non_stop_steps = [step for step in selected_steps if step.action != PlanAction.STOP]
    stop_steps = [step for step in selected_steps if step.action == PlanAction.STOP]
    limited_steps = non_stop_steps[: JEV_MAX_CHOICES - 1]
    limited_steps.append(stop_steps[0])
    return [
        BoundedActionOption(
            choice_id=f"a{index}",
            description=describe_bounded_action(step),
            step=step,
        )
        for index, step in enumerate(limited_steps)
    ]


def compact_observation(
    *,
    objective: str,
    instruction: str,
    world_state: WorldState,
    safety_flags: list[str],
    recent_results: list[dict[str, object]],
    completed_action_count: int,
    remaining_action_count: int,
) -> dict[str, object]:
    """Structured state for JEV. Camera bytes and motor commands stay out."""
    robot_pose = world_state.robot_pose
    observed_objects: list[dict[str, object]] = []
    for track in world_state.objects[:16]:
        observed_objects.append(
            {
                "track_id": track.track_id,
                "label": track.label,
                "attributes": dict(track.attributes),
                "range_body_lengths": _rounded(track.relative_range_body_lengths),
                "bearing_rad": _rounded(track.relative_bearing_rad),
                "moving": track.moving,
            }
        )
    return {
        "objective": objective,
        "instruction": instruction,
        "control_mode": "bounded_intent",
        "pose": None
        if robot_pose is None
        else {
            "x": round(robot_pose.x, 3),
            "y": round(robot_pose.y, 3),
            "heading_rad": round(robot_pose.heading_rad, 3),
        },
        "home": {
            "x": round(world_state.home_pose.x, 3),
            "y": round(world_state.home_pose.y, 3),
            "heading_rad": round(world_state.home_pose.heading_rad, 3),
        },
        "objects": observed_objects,
        "safety_flags": list(safety_flags),
        "recent": list(recent_results[-5:]),
        "completed_action_count": completed_action_count,
        "remaining_action_count": remaining_action_count,
    }


def pose_displacement(start_pose: Pose2D | None, end_pose: Pose2D | None) -> float | None:
    if start_pose is None or end_pose is None:
        return None
    return math.hypot(end_pose.x - start_pose.x, end_pose.y - start_pose.y)


def decision_is_stale(
    *,
    elapsed_s: float,
    displacement: float | None,
    stale_response_s: float,
    stale_displacement: float,
) -> bool:
    if elapsed_s > stale_response_s:
        return True
    return displacement is not None and displacement >= stale_displacement


def parse_jev_choice(payload: object, allowed_choice_ids: set[str]) -> str:
    if not isinstance(payload, dict):
        raise JevDecisionError("Invalid JEV action")
    answers = payload.get("answers")
    if not isinstance(answers, dict):
        raise JevDecisionError("Invalid JEV action")
    for question_name in ("action", "movement"):
        answer = answers.get(question_name)
        if not isinstance(answer, dict):
            continue
        choice = answer.get("choice")
        if isinstance(choice, str) and choice in allowed_choice_ids:
            return choice
    raise JevDecisionError("Invalid JEV action")


def active_cooldown_keys(cooldowns: dict[str, float], now_s: float) -> set[str]:
    return {action_name for action_name, expires_at_s in cooldowns.items() if expires_at_s > now_s}


class OfflineJevClient:
    """Same choice interface as live JEV, used when no API key is configured."""

    def __init__(self, model: str) -> None:
        self.source = "offline"
        self.model = model

    def __repr__(self) -> str:
        return f"OfflineJevClient(source={self.source}, model={self.model})"

    async def choose(
        self,
        observation: dict[str, object],
        options: list[BoundedActionOption],
    ) -> JevChoice:
        del observation
        if not options:
            raise JevDecisionError("Invalid JEV action")
        selected = next(
            (option for option in options if option.step.action != PlanAction.STOP),
            options[-1],
        )
        return JevChoice(
            choice_id=selected.choice_id,
            source=self.source,
            model=self.model,
            latency_ms=0,
        )


class HttpJevClient:
    def __init__(
        self,
        *,
        api_key: str,
        url: str,
        model: str,
        source: str,
        timeout_s: float,
    ) -> None:
        self._api_key = api_key
        self._url = url
        self.model = model
        self.source = source
        self._timeout_s = timeout_s

    def __repr__(self) -> str:
        return f"HttpJevClient(source={self.source}, model={self.model})"

    async def choose(
        self,
        observation: dict[str, object],
        options: list[BoundedActionOption],
    ) -> JevChoice:
        criteria = {option.choice_id: option.description for option in options}
        payload = {
            "model": self.model,
            "state": json.dumps(observation, separators=(",", ":"), sort_keys=True),
            "questions": {
                "action": {
                    "type": "choice",
                    "instructions": JEV_ACTION_INSTRUCTIONS,
                    "criteria": criteria,
                }
            },
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        started_at_s = monotonic()
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                response = await client.post(self._url, headers=headers, json=payload)
        except httpx.TimeoutException as error:
            raise JevTransientError("timeout") from error
        except httpx.TransportError as error:
            raise JevTransientError("transport") from error
        if response.status_code in _TRANSIENT_STATUS_CODES:
            raise JevTransientError(f"http_{response.status_code}")
        if response.status_code >= 400:
            raise JevDecisionError(f"Model HTTP {response.status_code}")
        try:
            response_payload: object = response.json()
        except json.JSONDecodeError as error:
            raise JevDecisionError("Invalid JEV action") from error
        choice_id = parse_jev_choice(response_payload, set(criteria))
        latency_ms = int((monotonic() - started_at_s) * 1000)
        return JevChoice(
            choice_id=choice_id,
            source=self.source,
            model=self.model,
            latency_ms=latency_ms,
        )


def create_jev_client(
    *,
    model: str,
    timeout_s: float,
    openrouter_api_key: str | None,
    typesafe_api_key: str | None,
) -> JevClient:
    openrouter_key = _nonempty_secret(openrouter_api_key)
    if openrouter_key is not None:
        return HttpJevClient(
            api_key=openrouter_key,
            url=_OPENROUTER_DECISIONS_URL,
            model=model,
            source="openrouter",
            timeout_s=timeout_s,
        )
    typesafe_key = _nonempty_secret(typesafe_api_key)
    if typesafe_key is not None:
        typesafe_model = model if "/" not in model else "jev-latest"
        return HttpJevClient(
            api_key=typesafe_key,
            url=_TYPESAFE_DECISIONS_URL,
            model=typesafe_model,
            source="typesafe",
            timeout_s=timeout_s,
        )
    return OfflineJevClient(model=model)


async def choose_bounded_action(
    *,
    client: JevClient,
    options_for_attempt: Callable[[], list[BoundedActionOption]],
    observation_for_attempt: Callable[[], dict[str, object]],
    pose_reader: Callable[[], Pose2D | None],
    stale_response_s: float,
    stale_displacement: float,
    max_attempts: int = JEV_MAX_ATTEMPTS,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    observe: Callable[[str, dict[str, object]], None] | None = None,
) -> tuple[BoundedActionOption, JevChoice]:
    """Ask for one legal action. Stale and transient failures share one attempt budget."""
    for attempt in range(1, max_attempts + 1):
        options = options_for_attempt()
        if not options:
            _observe(observe, "invalid", {"attempt": attempt})
            raise JevSelectionFailed("invalid")
        observation = observation_for_attempt()
        start_pose = pose_reader()
        started_at_s = monotonic()
        try:
            choice = await client.choose(observation, options)
        except JevTransientError as error:
            _observe(observe, "transient", {"attempt": attempt, "error_type": str(error)[:80]})
            if attempt >= max_attempts:
                raise JevSelectionFailed("transient") from error
            await sleep(float(2 ** (attempt - 1)))
            continue
        except JevDecisionError as error:
            _observe(observe, "invalid", {"attempt": attempt})
            raise JevSelectionFailed("invalid") from error
        elapsed_s = monotonic() - started_at_s
        displacement = pose_displacement(start_pose, pose_reader())
        if decision_is_stale(
            elapsed_s=elapsed_s,
            displacement=displacement,
            stale_response_s=stale_response_s,
            stale_displacement=stale_displacement,
        ):
            _observe(
                observe,
                "stale",
                {
                    "attempt": attempt,
                    "elapsed_s": round(elapsed_s, 3),
                    "displacement": None if displacement is None else round(displacement, 3),
                },
            )
            if attempt >= max_attempts:
                raise JevSelectionFailed("stale")
            await sleep(JEV_STALE_RETRY_S)
            continue
        selected = next(
            (option for option in options if option.choice_id == choice.choice_id),
            None,
        )
        if selected is None:
            _observe(observe, "invalid", {"attempt": attempt})
            raise JevSelectionFailed("invalid")
        return selected, choice
    raise JevSelectionFailed("transient")


def _observe(
    observe: Callable[[str, dict[str, object]], None] | None,
    kind: str,
    detail: dict[str, object],
) -> None:
    if observe is not None:
        observe(kind, detail)


def _rounded(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 3)


def _nonempty_secret(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return stripped
