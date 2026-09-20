from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

OpenAIReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"]


class BrainConfig(BaseModel):
    provider: str = "fake"
    model: str = "gpt-5"
    reasoning_effort: OpenAIReasoningEffort = "medium"
    request_timeout_s: float = 30.0
    perception_interval_s: float = 0.5


class ControlConfig(BaseModel):
    flycore_hz: int = 100
    executive_hz: int = 10
    intent_ttl_ms: int = 1500
    target_stale_ms: int = 500


class GeofenceConfig(BaseModel):
    x_min: float = -5.0
    x_max: float = 5.0
    y_min: float = -5.0
    y_max: float = 5.0


class SafetyConfig(BaseModel):
    max_speed_normalised: float = 0.65
    max_turn_normalised: float = 0.75
    contact_profile: str = "sim_conservative"
    contact_force_limit: float = 0.35
    geofence: GeofenceConfig = Field(default_factory=GeofenceConfig)
    stop_on_brain_disconnect: bool = False
    stop_on_executive_disconnect: bool = True
    record_images_by_default: bool = False


class SimulationConfig(BaseModel):
    backend: str = "mock"
    seed: int = 42
    render_operator_camera: bool = True
    allow_ground_truth_for_tests: bool = False


class FlyBotConfig(BaseModel):
    brain: BrainConfig = Field(default_factory=BrainConfig)
    control: ControlConfig = Field(default_factory=ControlConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    simulation: SimulationConfig = Field(default_factory=SimulationConfig)


class EnvironmentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-5", alias="OPENAI_MODEL")
    openai_reasoning_effort: OpenAIReasoningEffort = Field(
        default="medium", alias="OPENAI_REASONING_EFFORT"
    )
    brain_provider: str = Field(default="fake", alias="BRAIN_PROVIDER")
    body_backend: str = Field(default="", alias="BODY_BACKEND")
    simulation_backend: str = Field(default="mock", alias="SIMULATION_BACKEND")
    simulation_seed: int = Field(default=42, alias="SIMULATION_SEED")
    allow_ground_truth_for_tests: bool = Field(default=False, alias="ALLOW_GROUND_TRUTH_FOR_TESTS")
    test_ground_truth: bool = Field(default=False, alias="TEST_GROUND_TRUTH")
    render_operator_camera: bool = Field(default=True, alias="RENDER_OPERATOR_CAMERA")
    intent_ttl_ms: int = Field(default=1500, alias="INTENT_TTL_MS")
    max_speed_normalised: float = Field(default=0.65, alias="MAX_SPEED_NORMALISED")
    max_turn_normalised: float = Field(default=0.75, alias="MAX_TURN_NORMALISED")
    contact_force_limit: float = Field(default=0.35, alias="CONTACT_FORCE_LIMIT")
    record_images_by_default: bool = Field(default=False, alias="RECORD_IMAGES_BY_DEFAULT")
    session_recording: bool = Field(default=False, alias="SESSION_RECORDING")
    recordings_root: str = Field(default="artifacts/recordings", alias="RECORDINGS_ROOT")
    recording_frame_interval_s: float = Field(default=0.5, alias="RECORDING_FRAME_INTERVAL_S")


def _expand_environment_variables(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _expand_environment_variables(inner_value) for key, inner_value in value.items()
        }
    if isinstance(value, list):
        return [_expand_environment_variables(item) for item in value]
    if isinstance(value, str):
        return os.path.expandvars(value)
    return value


def load_config(config_path: Path | str = Path("configs/default.yaml")) -> FlyBotConfig:
    path_object = Path(config_path)
    raw_data = yaml.safe_load(path_object.read_text(encoding="utf-8"))
    expanded_data = _expand_environment_variables(raw_data)
    loaded_config = FlyBotConfig.model_validate(expanded_data)
    environment_settings = EnvironmentSettings()
    loaded_config.brain.model = environment_settings.openai_model
    loaded_config.brain.reasoning_effort = environment_settings.openai_reasoning_effort
    loaded_config.brain.provider = environment_settings.brain_provider
    selected_backend = (
        environment_settings.body_backend.strip()
        if environment_settings.body_backend.strip()
        else environment_settings.simulation_backend
    )
    loaded_config.simulation.backend = selected_backend
    loaded_config.simulation.seed = environment_settings.simulation_seed
    loaded_config.simulation.allow_ground_truth_for_tests = (
        environment_settings.allow_ground_truth_for_tests or environment_settings.test_ground_truth
    )
    loaded_config.simulation.render_operator_camera = environment_settings.render_operator_camera
    loaded_config.control.intent_ttl_ms = environment_settings.intent_ttl_ms
    loaded_config.safety.max_speed_normalised = environment_settings.max_speed_normalised
    loaded_config.safety.max_turn_normalised = environment_settings.max_turn_normalised
    loaded_config.safety.contact_force_limit = environment_settings.contact_force_limit
    loaded_config.safety.record_images_by_default = environment_settings.record_images_by_default
    return loaded_config
