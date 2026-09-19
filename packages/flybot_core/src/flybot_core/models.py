from __future__ import annotations

from enum import StrEnum
from time import monotonic_ns
from typing import Any

from pydantic import BaseModel, Field


class PlanAction(StrEnum):
    NAVIGATE = "NAVIGATE"
    SEARCH = "SEARCH"
    PATROL = "PATROL"
    OBSERVE = "OBSERVE"
    INSPECT = "INSPECT"
    APPROACH = "APPROACH"
    FOLLOW = "FOLLOW"
    TOUCH = "TOUCH"
    NUDGE = "NUDGE"
    RETREAT = "RETREAT"
    RETURN_HOME = "RETURN_HOME"
    WAIT = "WAIT"
    STOP = "STOP"


class OnFailurePolicy(StrEnum):
    RETRY = "RETRY"
    REPLAN = "REPLAN"
    STOP = "STOP"
    SKIP = "SKIP"


class IntentMode(StrEnum):
    STOP = "STOP"
    NAVIGATE = "NAVIGATE"
    APPROACH = "APPROACH"
    TURN = "TURN"
    FORWARD = "FORWARD"
    RETREAT = "RETREAT"
    FOLLOW = "FOLLOW"
    WAIT = "WAIT"
    TOUCH = "TOUCH"
    NUDGE = "NUDGE"
    RETURN_HOME = "RETURN_HOME"


class Pose2D(BaseModel):
    x: float
    y: float
    heading_rad: float = 0.0


class MotionConstraints(BaseModel):
    max_speed_normalised: float = 0.65
    max_turn_normalised: float = 0.75
    min_distance_body_lengths: float | None = None
    max_distance_body_lengths: float | None = None


class TouchProfile(BaseModel):
    limb: str = "right_front"
    max_force: float = 0.35
    dwell_s: float = 0.1
    one_shot: bool = True


class ObserveProfile(BaseModel):
    dwell_s: float = 2.0
    allow_recording: bool = False


class MotorCommand(BaseModel):
    forward: float = Field(default=0.0, ge=-1.0, le=1.0)
    turn: float = Field(default=0.0, ge=-1.0, le=1.0)
    stop: bool = False


class ContactSample(BaseModel):
    object_id: str
    force: float


class BodyState(BaseModel):
    pose: Pose2D
    linear_speed: float
    angular_speed: float
    joint_positions: dict[str, float]
    contacts: list[ContactSample]
    e_stop_latched: bool
    timestamp_ns: int


class HighRateObservation(BaseModel):
    pose: Pose2D
    contacts: list[ContactSample]
    timestamp_ns: int


class Frame(BaseModel):
    timestamp_ns: int
    mime_type: str = "image/png"
    data_url: str | None = None
    width: int = 320
    height: int = 240
    is_placeholder: bool = False


class GroundTruthObject(BaseModel):
    object_id: str
    label: str
    attributes: dict[str, str]
    pose: Pose2D
    moving: bool = False
    contactable: bool = True


class GroundTruthWorld(BaseModel):
    scene_revision: int
    objects: list[GroundTruthObject]
    home_pose: Pose2D


class ObjectTrack(BaseModel):
    track_id: str
    label: str
    attributes: dict[str, str]
    confidence: float
    relative_bearing_rad: float | None = None
    relative_range_body_lengths: float | None = None
    world_pose: Pose2D | None = None
    moving: bool
    last_seen_ns: int
    contactable: bool = True


class WorldState(BaseModel):
    robot_pose: Pose2D | None
    home_pose: Pose2D
    objects: list[ObjectTrack]
    current_region: str | None = None
    scene_revision: int = 0
    active_safety_flags: list[str] = Field(default_factory=list)


class TargetSpec(BaseModel):
    label: str | None = None
    attributes: dict[str, str] = Field(default_factory=dict)
    track_id: str | None = None


class PlanStep(BaseModel):
    step_id: str
    action: PlanAction
    target: TargetSpec | None
    parameters: dict[str, Any] = Field(default_factory=dict)
    timeout_s: float
    on_failure: OnFailurePolicy = OnFailurePolicy.REPLAN


class MissionPlan(BaseModel):
    mission_id: str
    objective: str
    assumptions: list[str] = Field(default_factory=list)
    steps: list[PlanStep]
    completion_summary_fields: list[str] = Field(default_factory=list)


class IntentCommand(BaseModel):
    intent_id: str
    mission_id: str
    step_id: str
    mode: IntentMode
    target_track_id: str | None = None
    target_pose: Pose2D | None = None
    desired_heading_rad: float | None = None
    desired_speed: float = 0.0
    stand_off_body_lengths: float | None = None
    touch: TouchProfile | None = None
    observe: ObserveProfile | None = None
    constraints: MotionConstraints = Field(default_factory=MotionConstraints)
    ttl_ms: int = 1500
    issued_at_ns: int = Field(default_factory=monotonic_ns)

    def is_expired(self, now_ns: int | None = None) -> bool:
        current_time_ns = monotonic_ns() if now_ns is None else now_ns
        return current_time_ns > self.issued_at_ns + (self.ttl_ms * 1_000_000)


class StepStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class StepResult(BaseModel):
    mission_id: str
    step_id: str
    status: StepStatus
    reason: str | None = None
    metrics: dict[str, float] = Field(default_factory=dict)


class ControllerStatus(BaseModel):
    mode: IntentMode = IntentMode.STOP
    active_intent_id: str | None = None
    detail: str = "idle"
