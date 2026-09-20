from typing import Any, cast

import pytest

from flybot_api import main
from flybot_api.service import RuntimeStatus
from flybot_core.models import Frame, WorldState


class FakeRuntime:
    def __init__(self) -> None:
        self.status_calls: list[bool] = []

    def status(self, include_frame_data_url: bool = True) -> RuntimeStatus:
        self.status_calls.append(include_frame_data_url)
        frame_data_url = (
            "data:image/png;base64,AAAABBBB"
            if include_frame_data_url
            else None
        )
        return RuntimeStatus(
            frame=Frame(timestamp_ns=123, data_url=frame_data_url, is_placeholder=False),
            safety_flags=[],
            mission_state={"mission_id": "mission_test"},
            world_state=WorldState(),
            body_state={},
            controller_state={},
            backend="mock",
        )


@pytest.mark.asyncio
async def test_api_state_omits_frame_data_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_runtime = FakeRuntime()
    monkeypatch.setattr(main, "runtime", fake_runtime)

    payload = cast(dict[str, Any], await main.api_state())

    assert payload["frame"]["data_url"] is None
    assert fake_runtime.status_calls == [False]


@pytest.mark.asyncio
async def test_api_state_can_include_frame_data_when_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_runtime = FakeRuntime()
    monkeypatch.setattr(main, "runtime", fake_runtime)

    payload = cast(dict[str, Any], await main.api_state(include_frame=True))

    assert payload["frame"]["data_url"] == "data:image/png;base64,AAAABBBB"
    assert fake_runtime.status_calls == [True]


@pytest.mark.asyncio
async def test_api_camera_returns_frame_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_runtime = FakeRuntime()
    monkeypatch.setattr(main, "runtime", fake_runtime)

    payload = cast(dict[str, Any], await main.api_camera())

    assert payload["frame"]["data_url"] == "data:image/png;base64,AAAABBBB"
    assert fake_runtime.status_calls == [True]
