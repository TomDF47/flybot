from __future__ import annotations

from typing import Protocol

from flybot_core.models import (
    BodyState,
    ControllerStatus,
    Frame,
    GroundTruthWorld,
    HighRateObservation,
    IntentCommand,
    MotorCommand,
)


class BodyAdapter(Protocol):
    async def reset(self, seed: int) -> BodyState: ...

    async def step(self, motor_command: MotorCommand, dt_s: float) -> BodyState: ...

    def high_rate_observation(self) -> HighRateObservation: ...

    def low_rate_frame(self) -> Frame: ...

    def world_snapshot(self) -> GroundTruthWorld | None: ...

    async def safe_stop(self) -> None: ...


class FlyCore(Protocol):
    def reset(self) -> None: ...

    def set_intent(self, intent_command: IntentCommand) -> None: ...

    def tick(self, high_rate_observation: HighRateObservation, dt_s: float) -> MotorCommand: ...

    def status(self) -> ControllerStatus: ...
