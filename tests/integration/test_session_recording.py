import asyncio
from pathlib import Path
from time import monotonic

import pytest

from flybot_api.service import FlyBotRuntime


@pytest.mark.asyncio
async def test_session_recording_persists_frames_and_telemetry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    recording_root = tmp_path / "recordings"
    monkeypatch.setenv("SESSION_RECORDING", "true")
    monkeypatch.setenv("RECORDINGS_ROOT", str(recording_root))
    monkeypatch.setenv("RECORDING_FRAME_INTERVAL_S", "0.1")
    monkeypatch.setenv("BODY_BACKEND", "mock")

    runtime = FlyBotRuntime()
    await runtime.start()
    try:
        started_at = monotonic()
        while monotonic() - started_at < 2.0:
            mission_state = runtime.status().mission_state
            if mission_state["recording_frame_count"] > 0:
                break
            await asyncio.sleep(0.05)
    finally:
        await runtime.stop()

    recording_directories = sorted(path for path in recording_root.iterdir() if path.is_dir())
    assert recording_directories
    recording_directory = recording_directories[0]
    frame_paths = sorted((recording_directory / "frames").glob("frame_*.png"))
    assert frame_paths
    telemetry_path = recording_directory / "telemetry.jsonl"
    assert telemetry_path.exists()
    assert telemetry_path.read_text(encoding="utf-8").strip() != ""
