from __future__ import annotations

from typing import Any, Literal

MissionProgress = Literal["running", "succeeded", "failed"]


def classify_mission_progress(mission_state: dict[str, Any]) -> MissionProgress:
    """Terminal state for one-shot plans and the JEV decision loop.

    While JEV is enabled, a finished step is not mission success. The loop keeps
    selecting actions until its phase is done.
    """
    decision_loop = mission_state.get("decision_loop")
    if isinstance(decision_loop, dict) and decision_loop.get("enabled") is True:
        phase = decision_loop.get("phase")
        if phase == "done":
            return "succeeded"
        if phase == "failed":
            return "failed"
        return "running"

    if mission_state.get("failed_step") is not None:
        return "failed"
    active_step_index = mission_state.get("active_step_index")
    plan_step_count = mission_state.get("plan_step_count")
    if (
        isinstance(active_step_index, int)
        and isinstance(plan_step_count, int)
        and plan_step_count > 0
        and active_step_index >= plan_step_count
    ):
        return "succeeded"
    return "running"
