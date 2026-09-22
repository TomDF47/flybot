from __future__ import annotations

import argparse
import asyncio
from time import monotonic

from flybot_api.service import FlyBotRuntime
from flybot_executive.progress import classify_mission_progress


async def run_demo(instruction: str, timeout_s: float) -> bool:
    runtime = FlyBotRuntime()
    await runtime.start()
    try:
        mission_id = await runtime.submit_instruction(instruction)
        started_at = monotonic()
        while monotonic() - started_at < timeout_s:
            mission_state = runtime.status().mission_state
            if mission_state["mission_id"] == mission_id:
                completed_steps = mission_state["completed_steps"]
                progress = classify_mission_progress(mission_state)
                print(
                    f"mission={mission_id} completed={len(completed_steps)} progress={progress}"
                )
                if progress == "failed":
                    return False
                if progress == "succeeded":
                    return True
            await asyncio.sleep(0.25)
        return False
    finally:
        await runtime.stop()


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run FlyBot scripted demo")
    parser.add_argument("--instruction", type=str, default="Walk to the red cube.")
    parser.add_argument("--timeout-s", type=float, default=20.0)
    parser.add_argument("--scripted-d1", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_arguments()
    selected_instruction = (
        "Walk to the red cube." if arguments.scripted_d1 else arguments.instruction
    )
    demo_success = asyncio.run(run_demo(selected_instruction, arguments.timeout_s))
    if not demo_success:
        raise SystemExit("Demo did not complete within timeout")
