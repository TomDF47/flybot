from __future__ import annotations

import math
from time import monotonic_ns

from flybot_core.models import GroundTruthWorld, ObjectTrack, Pose2D, TargetSpec, WorldState


class ObjectTrackResolver:
    def __init__(self, use_ground_truth_for_tests: bool) -> None:
        self._use_ground_truth_for_tests = use_ground_truth_for_tests

    def resolve_world_state(
        self, robot_pose: Pose2D, world_snapshot: GroundTruthWorld | None
    ) -> WorldState:
        if world_snapshot is None:
            return WorldState(
                robot_pose=robot_pose,
                home_pose=Pose2D(x=0.0, y=0.0, heading_rad=0.0),
                objects=[],
                scene_revision=0,
            )
        tracks: list[ObjectTrack] = []
        current_time_ns = monotonic_ns()
        for ground_truth_object in world_snapshot.objects:
            delta_x = ground_truth_object.pose.x - robot_pose.x
            delta_y = ground_truth_object.pose.y - robot_pose.y
            tracks.append(
                ObjectTrack(
                    track_id=ground_truth_object.object_id,
                    label=ground_truth_object.label,
                    attributes=ground_truth_object.attributes,
                    confidence=1.0 if self._use_ground_truth_for_tests else 0.85,
                    relative_bearing_rad=math.atan2(delta_y, delta_x) - robot_pose.heading_rad,
                    relative_range_body_lengths=math.hypot(delta_x, delta_y),
                    world_pose=ground_truth_object.pose,
                    moving=ground_truth_object.moving,
                    last_seen_ns=current_time_ns,
                    contactable=ground_truth_object.contactable,
                )
            )
        return WorldState(
            robot_pose=robot_pose,
            home_pose=world_snapshot.home_pose,
            objects=tracks,
            scene_revision=world_snapshot.scene_revision,
        )

    def resolve_target(
        self, world_state: WorldState, target_spec: TargetSpec | None
    ) -> ObjectTrack | None:
        if target_spec is None:
            return None
        if target_spec.track_id:
            for track in world_state.objects:
                if track.track_id == target_spec.track_id:
                    return track
        candidate_tracks = world_state.objects
        if target_spec.label is not None:
            candidate_tracks = [
                track for track in candidate_tracks if track.label == target_spec.label
            ]
        for key, value in target_spec.attributes.items():
            candidate_tracks = [
                track for track in candidate_tracks if track.attributes.get(key) == value
            ]
        if len(candidate_tracks) == 1:
            return candidate_tracks[0]
        if not candidate_tracks:
            return None
        return sorted(
            candidate_tracks, key=lambda track: track.relative_range_body_lengths or 999.0
        )[0]
