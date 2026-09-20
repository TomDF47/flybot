from __future__ import annotations

import base64
import io
import math
from dataclasses import dataclass
from time import monotonic_ns
from typing import Any

import numpy as np
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
    _mm_to_body_lengths = 0.05

    def __init__(self, config: FlyBotConfig) -> None:
        self._allow_ground_truth_for_tests = config.simulation.allow_ground_truth_for_tests
        self._seed = config.simulation.seed
        self._e_stop_latched = False
        self._step_count = 0
        self._scene_revision = 0
        self._home_pose = Pose2D(x=0.0, y=0.0, heading_rad=0.0)
        self._last_contacts: list[ContactSample] = []
        self._last_linear_speed = 0.0
        self._last_angular_speed = 0.0
        self._last_pose = Pose2D(x=0.0, y=0.0, heading_rad=0.0)
        self._last_pose_timestamp_ns = monotonic_ns()
        self._arena_objects: list[ArenaObject] = []
        self._moving_target_index = -1
        self._moving_target_mocap_id: int | None = None
        self._moving_target_body_name = "moving_target_body"
        self._fly_name = "fly"
        self._simulation: Any = None
        self._fly: Any = None
        self._renderer: Any = None
        self._mj: Any = None
        self._joint_names: list[str] = []
        self._muscle_names: list[str] = []
        self._actuator_type_muscle = None
        self._target_geom_names: set[str] = set()
        self._floor_geom_name = "floor"

        try:
            import flygym  # type: ignore[import-not-found,import-untyped]  # noqa: F401
            import mujoco as mj  # type: ignore[import-not-found,import-untyped]
            from flygym.compose import (  # type: ignore[import-not-found,import-untyped]
                ActuatorType,
                MusculoskeletalFly,
                MusculoskeletalWorld,
            )
            from flygym.simulation import (  # type: ignore[import-not-found,import-untyped]
                Simulation,
            )
        except ImportError as error:
            raise RuntimeError(
                "FlyGym 2.x backend is unavailable. Install with "
                "`uv sync --all-extras --dev` and ensure MuJoCo runtime libraries are present. "
                f"Docker fallback: {self._docker_flygym_hint()}"
            ) from error
        self._mj = mj
        self._ActuatorType = ActuatorType
        self._MusculoskeletalFly = MusculoskeletalFly
        self._MusculoskeletalWorld = MusculoskeletalWorld
        self._Simulation = Simulation
        self._initialize_simulation()

    def _initialize_simulation(self) -> None:
        try:
            self._fly = self._MusculoskeletalFly(name=self._fly_name)
            self._fly.add_vision()
            world = self._MusculoskeletalWorld(self._fly)
            self._setup_custom_arena(world)
            self._simulation = self._Simulation(world)
            self._renderer = self._simulation.set_renderer(
                cameras="scene",
                camera_res=(240, 320),
                output_fps=20,
                buffer_frames=True,
            )
            self._simulation.reset()
            self._joint_names = [str(joint_name) for joint_name in self._fly.get_jointdofs_order()]
            self._actuator_type_muscle = self._ActuatorType.MUSCLE
            self._muscle_names = [
                str(muscle_name)
                for muscle_name in self._fly.jointdof_to_mjcfactuator_by_type[
                    self._actuator_type_muscle
                ]
            ]
            self._resolve_moving_target_mocap_id()
            self._reset_arena_objects()
        except Exception as error:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "FlyGym 2.x initialization failed in this runtime. "
                f"Docker fallback: {self._docker_flygym_hint()} :: {error}"
            ) from error

    async def reset(self, seed: int) -> BodyState:
        assert self._simulation is not None
        self._seed = seed
        np.random.seed(seed)
        self._simulation.reset()
        self._e_stop_latched = False
        self._step_count = 0
        self._scene_revision += 1
        self._last_contacts = []
        self._last_linear_speed = 0.0
        self._last_angular_speed = 0.0
        self._reset_arena_objects()
        self._update_moving_target_pose()
        body_state = self._current_body_state()
        self._last_pose = body_state.pose
        self._last_pose_timestamp_ns = body_state.timestamp_ns
        return body_state

    async def step(self, motor_command: MotorCommand, dt_s: float) -> BodyState:
        assert self._simulation is not None
        self._step_count += 1
        self._update_moving_target_pose()
        commanded_forward = (
            0.0 if (self._e_stop_latched or motor_command.stop) else motor_command.forward
        )
        commanded_turn = 0.0 if (self._e_stop_latched or motor_command.stop) else motor_command.turn
        timestep = float(self._simulation.timestep)
        substeps = max(1, int(round(dt_s / timestep)))
        for _ in range(substeps):
            self._apply_muscle_controls(commanded_forward, commanded_turn)
            self._simulation.step()
        self._last_contacts = self._collect_contacts()
        return self._current_body_state()

    def high_rate_observation(self) -> HighRateObservation:
        body_state = self._current_body_state()
        return HighRateObservation(
            pose=body_state.pose,
            contacts=self._last_contacts,
            timestamp_ns=body_state.timestamp_ns,
        )

    def low_rate_frame(self) -> Frame:
        assert self._simulation is not None
        frame_array = None
        try:
            self._simulation.render_as_needed()
            if self._renderer is not None:
                frames_by_camera = self._renderer.frames.get("scene", [])
                if frames_by_camera:
                    frame_array = frames_by_camera[-1]
                    self._renderer.frames["scene"] = frames_by_camera[-2:]
        except Exception:
            frame_array = None
        if frame_array is None:
            frame_array = self._simulation.get_raw_vision(self._fly_name)[0]
        image = Image.fromarray(frame_array.astype(np.uint8))
        image_bytes = io.BytesIO()
        image.save(image_bytes, format="PNG")
        encoded = base64.b64encode(image_bytes.getvalue()).decode("utf-8")
        return Frame(
            timestamp_ns=monotonic_ns(),
            data_url=f"data:image/png;base64,{encoded}",
            width=frame_array.shape[1],
            height=frame_array.shape[0],
            is_placeholder=False,
        )

    def world_snapshot(self) -> GroundTruthWorld | None:
        if not self._allow_ground_truth_for_tests:
            return None
        ground_truth_objects: list[GroundTruthObject] = []
        for arena_object in self._arena_objects:
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
        self._apply_muscle_controls(0.0, 0.0)

    def _setup_custom_arena(self, world: Any) -> None:
        world_body = world.mjcf_root.worldbody
        self._arena_objects = [
            ArenaObject("target_red_cube", "cube", "red", "cube", x=1.7, y=1.2, moving=False),
            ArenaObject("target_blue_block", "block", "blue", "block", x=-1.7, y=1.1, moving=False),
            ArenaObject(
                "target_green_sphere", "target", "green", "sphere", x=0.0, y=-1.9, moving=True
            ),
            ArenaObject(
                "target_yellow_object", "object", "yellow", "object", x=1.8, y=-1.4, moving=False
            ),
            ArenaObject(
                "obstacle_gray_box",
                "obstacle",
                "gray",
                "box",
                x=0.0,
                y=2.0,
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
        self._moving_target_index = 2
        for arena_object in self._arena_objects:
            if arena_object.object_id == "target_green_sphere":
                moving_body = world_body.add_body(
                    name=self._moving_target_body_name,
                    mocap=1,
                    pos=[arena_object.x * 10.0, arena_object.y * 10.0, 2.0],
                )
                moving_body.add_geom(
                    name=arena_object.object_id,
                    type=self._mj.mjtGeom.mjGEOM_SPHERE,
                    size=[1.8],
                    rgba=[0.15, 0.85, 0.35, 1.0],
                    contype=1,
                    conaffinity=1,
                    friction=[0.8, 0.01, 0.001],
                )
                self._target_geom_names.add(arena_object.object_id)
                continue
            geom_type = (
                self._mj.mjtGeom.mjGEOM_BOX
                if arena_object.shape != "sphere"
                else self._mj.mjtGeom.mjGEOM_SPHERE
            )
            geom_size = [2.2, 2.2, 2.2] if geom_type == self._mj.mjtGeom.mjGEOM_BOX else [2.0]
            rgba = self._rgba_for_color(arena_object.color)
            world_body.add_geom(
                name=arena_object.object_id,
                type=geom_type,
                pos=[arena_object.x * 10.0, arena_object.y * 10.0, 2.2],
                size=geom_size,
                rgba=rgba,
                contype=1 if arena_object.contactable else 0,
                conaffinity=1 if arena_object.contactable else 0,
            )
            if arena_object.contactable:
                self._target_geom_names.add(arena_object.object_id)

    def _resolve_moving_target_mocap_id(self) -> None:
        assert self._simulation is not None
        body_id = self._mj.mj_name2id(
            self._simulation.mj_model,
            self._mj.mjtObj.mjOBJ_BODY,
            self._moving_target_body_name,
        )
        if body_id >= 0:
            mocap_id = int(self._simulation.mj_model.body_mocapid[body_id])
            self._moving_target_mocap_id = mocap_id if mocap_id >= 0 else None

    def _update_moving_target_pose(self) -> None:
        if self._moving_target_index < 0:
            return
        moving_phase = self._step_count / 20.0
        moving_x = math.sin(moving_phase) * 1.4
        moving_y = -1.9 + (math.cos(moving_phase) * 0.5)
        moving_target = self._arena_objects[self._moving_target_index]
        moving_target.x = moving_x
        moving_target.y = moving_y
        if self._simulation is None or self._moving_target_mocap_id is None:
            return
        self._simulation.mj_data.mocap_pos[self._moving_target_mocap_id, 0] = moving_x * 10.0
        self._simulation.mj_data.mocap_pos[self._moving_target_mocap_id, 1] = moving_y * 10.0
        self._simulation.mj_data.mocap_pos[self._moving_target_mocap_id, 2] = 2.0

    def _reset_arena_objects(self) -> None:
        for arena_object in self._arena_objects:
            if arena_object.object_id == "target_green_sphere":
                arena_object.x = 0.0
                arena_object.y = -1.9

    def _apply_muscle_controls(self, forward: float, turn: float) -> None:
        assert self._simulation is not None
        muscle_count = len(self._muscle_names)
        if muscle_count == 0:
            return
        baseline = 0.15
        forward_drive = max(-1.0, min(1.0, forward)) * 0.12
        turn_drive = max(-1.0, min(1.0, turn)) * 0.08
        phase_drive = math.sin(self._step_count / 8.0) * 0.02
        inputs = np.full((muscle_count,), baseline + phase_drive, dtype=np.float32)
        for index, muscle_name in enumerate(self._muscle_names):
            side_factor = 0.0
            if muscle_name.startswith("L"):
                side_factor = 1.0
            elif muscle_name.startswith("R"):
                side_factor = -1.0
            inputs[index] = baseline + forward_drive + (side_factor * turn_drive) + phase_drive
        inputs = np.clip(inputs, 0.0, 1.0)
        self._simulation.set_actuator_inputs(self._fly_name, self._actuator_type_muscle, inputs)

    def _collect_contacts(self) -> list[ContactSample]:
        assert self._simulation is not None
        contacts: list[ContactSample] = []
        for contact_index in range(self._simulation.mj_data.ncon):
            contact = self._simulation.mj_data.contact[contact_index]
            geom_1 = self._mj.mj_id2name(
                self._simulation.mj_model, self._mj.mjtObj.mjOBJ_GEOM, int(contact.geom1)
            )
            geom_2 = self._mj.mj_id2name(
                self._simulation.mj_model, self._mj.mjtObj.mjOBJ_GEOM, int(contact.geom2)
            )
            if geom_1 is None or geom_2 is None:
                continue
            target_geom_name = None
            if geom_1 in self._target_geom_names:
                target_geom_name = geom_1
            elif geom_2 in self._target_geom_names:
                target_geom_name = geom_2
            if target_geom_name is None:
                continue
            force_array = np.zeros(6, dtype=np.float64)
            self._mj.mj_contactForce(
                self._simulation.mj_model, self._simulation.mj_data, contact_index, force_array
            )
            force_magnitude = float(np.linalg.norm(force_array[:3]))
            contacts.append(ContactSample(object_id=target_geom_name, force=force_magnitude))
        return contacts

    def _current_body_state(self) -> BodyState:
        assert self._simulation is not None
        body_positions = self._simulation.get_body_positions(self._fly_name)
        body_rotations = self._simulation.get_body_rotations(self._fly_name)
        joint_angles = self._simulation.get_joint_angles(self._fly_name)
        timestamp_ns = monotonic_ns()
        thorax_position = body_positions[0]
        thorax_rotation = body_rotations[0]
        pose = Pose2D(
            x=float(thorax_position[0] * self._mm_to_body_lengths),
            y=float(thorax_position[1] * self._mm_to_body_lengths),
            heading_rad=self._quat_to_yaw(thorax_rotation),
        )
        elapsed_s = max(1e-6, (timestamp_ns - self._last_pose_timestamp_ns) / 1_000_000_000)
        linear_speed = (
            math.hypot(pose.x - self._last_pose.x, pose.y - self._last_pose.y) / elapsed_s
        )
        angular_speed = (pose.heading_rad - self._last_pose.heading_rad) / elapsed_s
        self._last_pose = pose
        self._last_pose_timestamp_ns = timestamp_ns
        self._last_linear_speed = linear_speed
        self._last_angular_speed = angular_speed
        return BodyState(
            pose=pose,
            linear_speed=linear_speed,
            angular_speed=angular_speed,
            joint_positions={
                joint_name: float(joint_angles[index])
                for index, joint_name in enumerate(self._joint_names)
            },
            contacts=self._last_contacts,
            e_stop_latched=self._e_stop_latched,
            timestamp_ns=timestamp_ns,
        )

    @staticmethod
    def _quat_to_yaw(quaternion: np.ndarray) -> float:
        if quaternion.shape[0] < 4:
            return 0.0
        quaternion_w, quaternion_x, quaternion_y, quaternion_z = (
            float(quaternion[0]),
            float(quaternion[1]),
            float(quaternion[2]),
            float(quaternion[3]),
        )
        norm = math.sqrt(
            (quaternion_w * quaternion_w)
            + (quaternion_x * quaternion_x)
            + (quaternion_y * quaternion_y)
            + (quaternion_z * quaternion_z)
        )
        if norm <= 1e-9:
            return 0.0
        quaternion_w /= norm
        quaternion_x /= norm
        quaternion_y /= norm
        quaternion_z /= norm
        siny_cosp = 2.0 * ((quaternion_w * quaternion_z) + (quaternion_x * quaternion_y))
        cosy_cosp = 1.0 - (2.0 * ((quaternion_y * quaternion_y) + (quaternion_z * quaternion_z)))
        return math.atan2(siny_cosp, cosy_cosp)

    @staticmethod
    def _rgba_for_color(color: str) -> list[float]:
        rgba_map = {
            "red": [0.9, 0.2, 0.2, 1.0],
            "blue": [0.2, 0.35, 0.9, 1.0],
            "green": [0.2, 0.85, 0.25, 1.0],
            "yellow": [0.9, 0.85, 0.2, 1.0],
            "gray": [0.6, 0.6, 0.6, 1.0],
            "white": [0.95, 0.95, 0.95, 1.0],
        }
        return rgba_map.get(color, [1.0, 1.0, 1.0, 1.0])

    @staticmethod
    def _docker_flygym_hint() -> str:
        return (
            "docker build -f docker/Dockerfile . && "
            "docker run --rm -e BODY_BACKEND=flygym "
            "-e SIMULATION_BACKEND=flygym <image-id> make test"
        )


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
        "flygym": (
            "FlyGym 2.x musculoskeletal simulation backend "
            "with custom arena and camera telemetry."
        ),
    }
