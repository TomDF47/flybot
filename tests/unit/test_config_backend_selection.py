from pathlib import Path

import pytest

from flybot_core.config import load_config


def test_body_backend_override_takes_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIMULATION_BACKEND", "mock")
    monkeypatch.setenv("BODY_BACKEND", "flygym")
    config = load_config(Path("configs/default.yaml"))
    assert config.simulation.backend == "flygym"


def test_simulation_backend_used_when_body_backend_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BODY_BACKEND", "")
    monkeypatch.setenv("SIMULATION_BACKEND", "mock")
    config = load_config(Path("configs/default.yaml"))
    assert config.simulation.backend == "mock"
