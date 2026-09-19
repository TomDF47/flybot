from __future__ import annotations

import base64
import io
import math
from dataclasses import dataclass
from time import monotonic_ns
from typing import Any

from PIL import Image, ImageDraw

from flybot_core.config import FlyBotConfig
from flybot_core.models import (
    BodyState,
    ContactSample,
    Frame,
    GroundTruthObject,
    GroundTruthWorld,
    HighRateObservation,
    MotorCommand,
    Pose2D,
)
from flybot_core.protocols import BodyAdapter


@dataclass
class ArenaObject:
    object_id: str
    label: str
    color: str
    shape: str
    x: float
    y: float
    moving: bool = False
    contactable: bool = True


class MockBodyAdapter(BodyAdapter):
    def __init__(self, allow_ground_truth_for_tests: bool, seed: int) -> None:
        self._allow_ground_truth_for_tests = allow_ground_truth_for_tests
        self._seed = seed
        self._pose = Pose2D(x=0.0, y=0.0, heading_rad=0.0)
        self._linear_speed = 0.0
        self._angular_speed = 0.0
        self._e_stop_latched = False
        self._step_count = 0
        self._scene_revision = 0
        self._objects: list[ArenaObject] = []
        self._last_contacts: list[ContactSample] = []
        self._home_pose = Pose2D(x=0.0, y=0.0, heading_rad=0.0)
        self._reset_world()

    def _reset_world(self) -> None:
        self._objects = [
            ArenaObject("target_red_cube", "cube", "red", "cube", x=2.0, y=1.5, moving=False),
            ArenaObject("target_blue_block", "block", "blue", "block", x=-2.0, y=1.0, moving=False),
            ArenaObject(
                "target_green_sphere", "target", "green", "sphere", x=0.0, y=-2.5, moving=True
            ),
            ArenaObject(
                "target_yellow_object",
                "object",
                "yellow",
                "object",
                x=2.5,
                y=-1.5,
                moving=False,
            ),
            ArenaObject(
                "obstacle_gray_box",
                "obstacle",
                "gray",
                "box",
                x=0.0,
                y=2.5,
                moving=False,
                contactable=False,
            ),
            ArenaObject(
                "home_marker",
                "home",
                "white",
                "marker",
                x=0.0,
                y=0.0,
                moving=False,
                contactable=False,
            ),
        ]
        self._scene_revision += 1

    async def reset(self, seed: int) -> BodyState:
        self._seed = seed
        self._pose = Pose2D(x=0.0, y=0.0, heading_rad=0.0)
        self._linear_speed = 0.0
        self._angular_speed = 0.0
        self._e_stop_latched = False
        self._step_count = 0
        self._last_contacts = []
        self._reset_world()
        return self._current_body_state()

    async def step(self, motor_command: MotorCommand, dt_s: float) -> BodyState:
        self._step_count += 1
        self._update_moving_target()
        if self._e_stop_latched or motor_command.stop:
            self._linear_speed = 0.0
            self._angular_speed = 0.0
            self._last_contacts = self._compute_contacts()
            return self._current_body_state()

        self._linear_speed = max(-1.0, min(1.0, motor_command.forward))
        self._angular_speed = max(-1.0, min(1.0, motor_command.turn))
        self._pose.heading_rad = self._wrap_angle(
            self._pose.heading_rad + self._angular_speed * dt_s * 2.5
        )
        delta_x = math.cos(self._pose.heading_rad) * self._linear_speed * dt_s
        delta_y = math.sin(self._pose.heading_rad) * self._linear_speed * dt_s
        self._pose.x += delta_x
        self._pose.y += delta_y
        self._last_contacts = self._compute_contacts()
        return self._current_body_state()

    def high_rate_observation(self) -> HighRateObservation:
        return HighRateObservation(
            pose=self._pose,
            contacts=self._last_contacts,
            timestamp_ns=monotonic_ns(),
        )

    def low_rate_frame(self) -> Frame:
        image = Image.new("RGB", (320, 240), color=(25, 25, 35))
        draw = ImageDraw.Draw(image)
        self._draw_grid(draw)
        for arena_object in self._objects:
            self._draw_object(draw, arena_object)
        self._draw_robot(draw)
        image_bytes = io.BytesIO()
        image.save(image_bytes, format="PNG")
        encoded = base64.b64encode(image_bytes.getvalue()).decode("utf-8")
        return Frame(
            timestamp_ns=monotonic_ns(),
            data_url=f"data:image/png;base64,{encoded}",
            is_placeholder=False,
        )

    def world_snapshot(self) -> GroundTruthWorld | None:
        if not self._allow_ground_truth_for_tests:
            return None
        ground_truth_objects: list[GroundTruthObject] = []
        for arena_object in self._objects:
            ground_truth_objects.append(
                GroundTruthObject(
                    object_id=arena_object.object_id,
                    label=arena_object.label,
                    attributes={"color": arena_object.color, "shape": arena_object.shape},
                    pose=Pose2D(x=arena_object.x, y=arena_object.y, heading_rad=0.0),
                    moving=arena_object.moving,
                    contactable=arena_object.contactable,
                )
            )
        return GroundTruthWorld(
            scene_revision=self._scene_revision,
            objects=ground_truth_objects,
            home_pose=self._home_pose,
        )

    async def safe_stop(self) -> None:
        self._e_stop_latched = True
        self._linear_speed = 0.0
        self._angular_speed = 0.0

    def _update_moving_target(self) -> None:
        moving_phase = self._step_count / 20.0
        for arena_object in self._objects:
            if arena_object.object_id == "target_green_sphere":
                arena_object.x = math.sin(moving_phase) * 1.5
                arena_object.y = -2.5 + (math.cos(moving_phase) * 0.5)

    def _compute_contacts(self) -> list[ContactSample]:
        contacts: list[ContactSample] = []
        for arena_object in self._objects:
            if not arena_object.contactable:
                continue
            distance = math.hypot(self._pose.x - arena_object.x, self._pose.y - arena_object.y)
            if distance < 0.4:
                contacts.append(
                    ContactSample(
                        object_id=arena_object.object_id,
                        force=max(0.0, (0.4 - distance) * 2.0),
                    )
                )
        return contacts

    def _current_body_state(self) -> BodyState:
        return BodyState(
            pose=self._pose,
            linear_speed=self._linear_speed,
            angular_speed=self._angular_speed,
            joint_positions={
                "left_front_coxa": 0.0,
                "right_front_coxa": 0.0,
                "left_mid_coxa": 0.0,
                "right_mid_coxa": 0.0,
                "left_hind_coxa": 0.0,
                "right_hind_coxa": 0.0,
            },
            contacts=self._last_contacts,
            e_stop_latched=self._e_stop_latched,
            timestamp_ns=monotonic_ns(),
        )

    @staticmethod
    def _wrap_angle(angle_rad: float) -> float:
        return math.atan2(math.sin(angle_rad), math.cos(angle_rad))

    def _draw_grid(self, draw: ImageDraw.ImageDraw) -> None:
        for x in range(0, 321, 40):
            draw.line((x, 0, x, 240), fill=(40, 40, 55))
        for y in range(0, 241, 40):
            draw.line((0, y, 320, y), fill=(40, 40, 55))

    def _draw_object(self, draw: ImageDraw.ImageDraw, arena_object: ArenaObject) -> None:
        pixel_x, pixel_y = self._world_to_pixel(arena_object.x, arena_object.y)
        color_map = {
            "red": (220, 60, 60),
            "blue": (80, 120, 255),
            "green": (60, 220, 120),
            "yellow": (236, 211, 61),
            "gray": (150, 150, 150),
            "white": (240, 240, 240),
        }
        color = color_map.get(arena_object.color, (255, 255, 255))
        draw.rectangle(
            (pixel_x - 8, pixel_y - 8, pixel_x + 8, pixel_y + 8), fill=color, outline=(0, 0, 0)
        )
        draw.text((pixel_x + 10, pixel_y - 8), arena_object.label, fill=(240, 240, 240))

    def _draw_robot(self, draw: ImageDraw.ImageDraw) -> None:
        center_x, center_y = self._world_to_pixel(self._pose.x, self._pose.y)
        draw.ellipse((center_x - 6, center_y - 6, center_x + 6, center_y + 6), fill=(255, 220, 0))
        heading_x = center_x + int(math.cos(self._pose.heading_rad) * 14)
        heading_y = center_y + int(math.sin(self._pose.heading_rad) * 14)
        draw.line((center_x, center_y, heading_x, heading_y), fill=(255, 220, 0), width=2)

    @staticmethod
    def _world_to_pixel(world_x: float, world_y: float) -> tuple[int, int]:
        pixel_x = int((world_x + 5.0) * 32)
        pixel_y = int((5.0 - world_y) * 24)
        return pixel_x, pixel_y


class FlyGymBodyAdapter(BodyAdapter):
    def __init__(self, _config: FlyBotConfig) -> None:
        try:
            import flygym as _flygym  # type: ignore[import-untyped]  # noqa: F401
        except ImportError as error:
            raise RuntimeError(
                "FlyGym 2.x is not installed in this environment. Install with "
                "`uv sync --all-extras --dev` and ensure MuJoCo-compatible runtime "
                "libraries are present."
            ) from error
        raise NotImplementedError(
            "FlyGymBodyAdapter wiring is pending API verification in this environment. "
            "Use SIMULATION_BACKEND=mock for deterministic M0/M1 operation."
        )

    async def reset(self, seed: int) -> BodyState:
        raise NotImplementedError

    async def step(self, motor_command: MotorCommand, dt_s: float) -> BodyState:
        raise NotImplementedError

    def high_rate_observation(self) -> HighRateObservation:
        raise NotImplementedError

    def low_rate_frame(self) -> Frame:
        raise NotImplementedError

    def world_snapshot(self) -> GroundTruthWorld | None:
        raise NotImplementedError

    async def safe_stop(self) -> None:
        raise NotImplementedError


def create_body_adapter(config: FlyBotConfig) -> BodyAdapter:
    if config.simulation.backend == "flygym":
        return FlyGymBodyAdapter(config)
    if config.simulation.backend == "mock":
        return MockBodyAdapter(
            allow_ground_truth_for_tests=config.simulation.allow_ground_truth_for_tests,
            seed=config.simulation.seed,
        )
    raise ValueError(f"Unsupported simulation backend: {config.simulation.backend}")


def adapter_capabilities() -> dict[str, Any]:
    return {
        "mock": "Deterministic 2D embodied simulation with camera/telemetry.",
        "flygym": "Stub requiring runtime API verification in this environment.",
    }
