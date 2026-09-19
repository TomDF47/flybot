from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from time import monotonic

from flybot_api.service import FlyBotRuntime


@dataclass(frozen=True)
class DemoCase:
    demo_id: str
    instruction: str
    timeout_s: float


V01_DEMOS = [
    DemoCase("D1", "Walk to the red cube.", 20.0),
    DemoCase("D2", "Patrol the arena and tell me if anything changes.", 25.0),
    DemoCase("D3", "Find the blue block and touch it once with your right front leg.", 25.0),
    DemoCase(
        "D4",
        "Follow the moving green target but do not get closer than one body length.",
        25.0,
    ),
    DemoCase("D5", "Inspect the yellow object, circle it, then return home.", 30.0),
    DemoCase("D6", "Stop now.", 10.0),
]


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run FlyBot v0.1 full demo suite D1-D6")
    parser.add_argument(
        "--continue-on-failure",
        action="store_true",
        help="Run remaining demos even if one fails",
    )
    return parser.parse_args()


async def run_suite(continue_on_failure: bool) -> dict[str, bool]:
    outcomes: dict[str, bool] = {}
    for demo_case in V01_DEMOS:
        print(f"[{demo_case.demo_id}] {demo_case.instruction}")
        success = await _run_single_demo(demo_case.instruction, demo_case.timeout_s)
        outcomes[demo_case.demo_id] = success
        print(f"[{demo_case.demo_id}] success={success}")
        if not success and not continue_on_failure:
            break
    return outcomes


async def _run_single_demo(instruction: str, timeout_s: float) -> bool:
    runtime = FlyBotRuntime()
    await runtime.start()
    try:
        mission_id = await runtime.submit_instruction(instruction)
        started_at = monotonic()
        while monotonic() - started_at < timeout_s:
            mission_state = runtime.status().mission_state
            if mission_state["mission_id"] == mission_id:
                completed_steps = mission_state["completed_steps"]
                failed_step = mission_state["failed_step"]
                current_plan = runtime.executive.current_state().plan
                print(
                    f"mission={mission_id} completed={len(completed_steps)} "
                    f"failed={failed_step is not None}"
                )
                if failed_step is not None:
                    return False
                if (
                    current_plan is not None
                    and mission_state["active_step_index"] >= len(current_plan.steps)
                ):
                    return True
            await asyncio.sleep(0.25)
        return False
    finally:
        await runtime.stop()


def main() -> None:
    arguments = parse_arguments()
    outcomes = asyncio.run(run_suite(arguments.continue_on_failure))
    failed_demo_ids = [demo_id for demo_id, success in outcomes.items() if not success]
    print(f"Demo outcomes: {outcomes}")
    if failed_demo_ids:
        raise SystemExit(f"Failed demos: {', '.join(failed_demo_ids)}")


if __name__ == "__main__":
    main()
