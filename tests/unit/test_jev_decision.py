import asyncio
import json
from collections.abc import Awaitable, Callable, Coroutine

import httpx
import pytest

from flybot_brain.jev import (
    JEV_DEFAULT_MODEL,
    JEV_RUNNING_STATUS,
    BoundedActionOption,
    HttpJevClient,
    JevChoice,
    JevDecisionError,
    JevSelectionFailed,
    JevTransientError,
    OfflineJevClient,
    action_key,
    build_action_options,
    choose_bounded_action,
    compact_observation,
    create_jev_client,
    decision_is_stale,
    parse_jev_choice,
)
from flybot_core.config import load_config
from flybot_core.models import (
    ObjectTrack,
    OnFailurePolicy,
    PlanAction,
    PlanStep,
    Pose2D,
    WorldState,
)
from flybot_executive.progress import classify_mission_progress


def _step(action: PlanAction, step_id: str = "step_1") -> PlanStep:
    return PlanStep(
        step_id=step_id,
        action=action,
        target=None,
        parameters={},
        timeout_s=5.0,
        on_failure=OnFailurePolicy.REPLAN,
    )


def _navigate_option() -> BoundedActionOption:
    return BoundedActionOption(
        choice_id="a0",
        description="NAVIGATE",
        step=_step(PlanAction.NAVIGATE),
    )


def test_jev_is_enabled_with_default_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JEV_ENABLED", raising=False)
    monkeypatch.delenv("JEV_MODEL", raising=False)
    loaded_config = load_config("configs/default.yaml")
    assert loaded_config.brain.jev_enabled is True
    assert loaded_config.brain.jev_model == JEV_DEFAULT_MODEL


def test_jev_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JEV_ENABLED", "false")
    monkeypatch.setenv("JEV_MODEL", "typesafe/jev-1.13")
    loaded_config = load_config("configs/default.yaml")
    assert loaded_config.brain.jev_enabled is False
    assert loaded_config.brain.jev_model == "typesafe/jev-1.13"


def test_running_status_text_is_explicit() -> None:
    assert JEV_RUNNING_STATUS == "Running in a loop until you are done"


def test_hazard_and_cooldown_limit_the_choice_set() -> None:
    navigate = _step(PlanAction.NAVIGATE, "navigate")
    retreat = _step(PlanAction.RETREAT, "retreat")
    options = build_action_options(
        [navigate, retreat],
        cooled_down_keys={action_key(navigate)},
        safety_flags=["GEOFENCE_STOP"],
    )
    offered_actions = [option.step.action for option in options]
    assert PlanAction.NAVIGATE not in offered_actions
    assert offered_actions[0] == PlanAction.RETREAT
    assert offered_actions[-1] == PlanAction.STOP
    assert len(options) <= 20
    assert options[0].choice_id == "a0"


def test_compact_observation_omits_motors_and_frames() -> None:
    world_state = WorldState(
        robot_pose=Pose2D(x=1.0, y=2.0, heading_rad=0.2),
        objects=[
            ObjectTrack(
                track_id="cube_red",
                label="cube",
                attributes={"color": "red"},
                confidence=1.0,
                relative_range_body_lengths=1.2,
                world_pose=Pose2D(x=2.0, y=2.0, heading_rad=0.0),
            )
        ],
    )
    observation = compact_observation(
        objective="Walk to the red cube.",
        instruction="Walk to the red cube.",
        world_state=world_state,
        safety_flags=[],
        recent_results=[{"action": "NAVIGATE", "result": "done"}],
        completed_action_count=0,
        remaining_action_count=1,
    )
    encoded = json.dumps(observation)
    assert "torque" not in encoded
    assert "data_url" not in encoded
    assert "joint" not in encoded
    assert observation["control_mode"] == "bounded_intent"
    assert observation["objective"] == "Walk to the red cube."


def test_stale_decision_uses_time_or_displacement() -> None:
    assert decision_is_stale(
        elapsed_s=5.1,
        displacement=0.0,
        stale_response_s=5.0,
        stale_displacement=0.8,
    )
    assert decision_is_stale(
        elapsed_s=0.1,
        displacement=0.8,
        stale_response_s=5.0,
        stale_displacement=0.8,
    )
    assert not decision_is_stale(
        elapsed_s=0.1,
        displacement=0.2,
        stale_response_s=5.0,
        stale_displacement=0.8,
    )


def test_parse_jev_choice_rejects_unknown_actions() -> None:
    assert parse_jev_choice({"answers": {"action": {"choice": "a1"}}}, {"a0", "a1"}) == "a1"
    with pytest.raises(JevDecisionError, match="Invalid JEV action"):
        parse_jev_choice({"answers": {"action": {"choice": "fly"}}}, {"a0"})


def test_offline_selector_prefers_the_next_action_then_stop() -> None:
    client = OfflineJevClient(model=JEV_DEFAULT_MODEL)
    stop_option = BoundedActionOption("a1", "STOP", _step(PlanAction.STOP, "stop"))
    navigate_option = _navigate_option()

    async def choose_next() -> str:
        choice = await client.choose({}, [navigate_option, stop_option])
        return choice.choice_id

    assert asyncio_run(choose_next()) == "a0"
    assert "secret" not in repr(client)


def test_create_client_keeps_keys_out_of_repr() -> None:
    offline_client = create_jev_client(
        model=JEV_DEFAULT_MODEL,
        timeout_s=10.0,
        openrouter_api_key=None,
        typesafe_api_key="  ",
    )
    assert isinstance(offline_client, OfflineJevClient)
    live_client = create_jev_client(
        model=JEV_DEFAULT_MODEL,
        timeout_s=10.0,
        openrouter_api_key="super-secret-key",
        typesafe_api_key=None,
    )
    assert isinstance(live_client, HttpJevClient)
    assert live_client.source == "openrouter"
    assert live_client.model == JEV_DEFAULT_MODEL
    assert "super-secret-key" not in repr(live_client)
    typesafe_client = create_jev_client(
        model=JEV_DEFAULT_MODEL,
        timeout_s=10.0,
        openrouter_api_key=None,
        typesafe_api_key="typesafe-secret",
    )
    assert isinstance(typesafe_client, HttpJevClient)
    assert typesafe_client.source == "typesafe"
    assert typesafe_client.model == "jev-latest"
    assert "typesafe-secret" not in repr(typesafe_client)


def test_http_client_posts_a_bounded_choice_request(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 200

        def json(self) -> object:
            return {"answers": {"action": {"choice": "a0"}}}

    class FakeAsyncClient:
        def __init__(self, timeout: float) -> None:
            captured["timeout"] = timeout

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(
            self,
            url: str,
            headers: dict[str, str],
            json: dict[str, object],
        ) -> FakeResponse:
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    client = HttpJevClient(
        api_key="super-secret-key",
        url="https://openrouter.ai/api/alpha/decisions",
        model=JEV_DEFAULT_MODEL,
        source="openrouter",
        timeout_s=10.0,
    )

    async def choose() -> JevChoice:
        return await client.choose(
            {"objective": "Walk to the red cube.", "control_mode": "bounded_intent"},
            [_navigate_option()],
        )

    choice = asyncio_run(choose())
    assert choice.choice_id == "a0"
    assert choice.source == "openrouter"
    request_payload = captured["json"]
    assert isinstance(request_payload, dict)
    encoded_request = json.dumps(request_payload)
    assert "super-secret-key" not in encoded_request
    state_payload = json.loads(str(request_payload["state"]))
    assert isinstance(state_payload, dict)
    assert "data_url" not in state_payload
    assert "torque" not in state_payload
    assert request_payload["model"] == JEV_DEFAULT_MODEL
    questions = request_payload["questions"]
    assert isinstance(questions, dict)
    action_question = questions["action"]
    assert isinstance(action_question, dict)
    assert action_question["type"] == "choice"
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["Authorization"] == "Bearer super-secret-key"


def test_http_client_treats_service_failure_as_transient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResponse:
        status_code = 503

        def json(self) -> object:
            return {"error": "unavailable"}

    class FakeAsyncClient:
        def __init__(self, timeout: float) -> None:
            del timeout

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(
            self,
            url: str,
            headers: dict[str, str],
            json: dict[str, object],
        ) -> FakeResponse:
            del url, headers, json
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    client = HttpJevClient(
        api_key="super-secret-key",
        url="https://openrouter.ai/api/alpha/decisions",
        model=JEV_DEFAULT_MODEL,
        source="openrouter",
        timeout_s=10.0,
    )

    async def choose() -> None:
        await client.choose({}, [_navigate_option()])

    with pytest.raises(JevTransientError):
        asyncio_run(choose())


def test_invalid_choice_stops_without_a_fallback() -> None:
    sleeps: list[float] = []

    class RejectingClient:
        source = "offline"
        model = JEV_DEFAULT_MODEL

        async def choose(
            self,
            observation: dict[str, object],
            options: list[BoundedActionOption],
        ) -> JevChoice:
            del observation, options
            raise JevDecisionError("Invalid JEV action")

    async def choose() -> None:
        await choose_bounded_action(
            client=RejectingClient(),
            options_for_attempt=lambda: [_navigate_option()],
            observation_for_attempt=dict,
            pose_reader=lambda: None,
            stale_response_s=5.0,
            stale_displacement=0.8,
            sleep=_record_sleep(sleeps),
        )

    with pytest.raises(JevSelectionFailed, match="invalid"):
        asyncio_run(choose())
    assert sleeps == []


def test_stale_answers_are_discarded_until_a_fresh_one() -> None:
    sleeps: list[float] = []
    poses = [
        Pose2D(x=0.0, y=0.0, heading_rad=0.0),
        Pose2D(x=1.0, y=0.0, heading_rad=0.0),
        Pose2D(x=1.0, y=0.0, heading_rad=0.0),
        Pose2D(x=1.0, y=0.0, heading_rad=0.0),
    ]
    events: list[str] = []

    class StableClient:
        source = "offline"
        model = JEV_DEFAULT_MODEL

        async def choose(
            self,
            observation: dict[str, object],
            options: list[BoundedActionOption],
        ) -> JevChoice:
            del observation, options
            return JevChoice(
                choice_id="a0",
                source=self.source,
                model=self.model,
                latency_ms=1,
            )

    def read_pose() -> Pose2D:
        return poses.pop(0)

    async def choose() -> str:
        selected, _choice = await choose_bounded_action(
            client=StableClient(),
            options_for_attempt=lambda: [_navigate_option()],
            observation_for_attempt=dict,
            pose_reader=read_pose,
            stale_response_s=5.0,
            stale_displacement=0.8,
            sleep=_record_sleep(sleeps),
            observe=lambda kind, _detail: events.append(kind),
        )
        return selected.step.action.value

    assert asyncio_run(choose()) == "NAVIGATE"
    assert events == ["stale"]
    assert sleeps == [0.25]


def test_transient_failures_share_the_attempt_budget() -> None:
    sleeps: list[float] = []
    attempts = {"count": 0}

    class FlakyClient:
        source = "openrouter"
        model = JEV_DEFAULT_MODEL

        async def choose(
            self,
            observation: dict[str, object],
            options: list[BoundedActionOption],
        ) -> JevChoice:
            del observation, options
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise JevTransientError("timeout")
            return JevChoice(choice_id="a0", source=self.source, model=self.model, latency_ms=4)

    async def choose() -> int:
        _selected, choice = await choose_bounded_action(
            client=FlakyClient(),
            options_for_attempt=lambda: [_navigate_option()],
            observation_for_attempt=dict,
            pose_reader=lambda: None,
            stale_response_s=5.0,
            stale_displacement=0.8,
            sleep=_record_sleep(sleeps),
        )
        return choice.latency_ms

    assert asyncio_run(choose()) == 4
    assert sleeps == [1.0, 2.0]


def test_classify_mission_progress_waits_for_the_loop_to_finish() -> None:
    running = classify_mission_progress(
        {
            "failed_step": None,
            "active_step_index": 1,
            "plan_step_count": 1,
            "decision_loop": {"enabled": True, "phase": "running"},
        }
    )
    done = classify_mission_progress(
        {
            "failed_step": None,
            "active_step_index": 2,
            "plan_step_count": 2,
            "decision_loop": {"enabled": True, "phase": "done"},
        }
    )
    one_shot = classify_mission_progress(
        {
            "failed_step": None,
            "active_step_index": 1,
            "plan_step_count": 1,
            "decision_loop": {"enabled": False, "phase": "idle"},
        }
    )
    assert running == "running"
    assert done == "succeeded"
    assert one_shot == "succeeded"


def _record_sleep(sleeps: list[float]) -> Callable[[float], Awaitable[None]]:
    async def sleep(delay_s: float) -> None:
        sleeps.append(delay_s)

    return sleep


def asyncio_run[T](coroutine: Coroutine[object, object, T]) -> T:
    return asyncio.run(coroutine)
