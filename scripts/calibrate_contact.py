from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from statistics import mean
from time import monotonic

from flybot_api.service import FlyBotRuntime


async def run_calibration(trials: int) -> dict[str, float | int | None]:
    runtime = FlyBotRuntime()
    await runtime.start()
    contact_forces: list[float] = []
    try:
        for trial_index in range(trials):
            await runtime.reset(seed=42 + trial_index)
            mission_id = await runtime.submit_instruction(
                "Find the blue block and touch it once with your right front leg."
            )
            started_at = monotonic()
            while monotonic() - started_at < 20.0:
                mission_state = runtime.status().mission_state
                if mission_state["mission_id"] != mission_id:
                    await asyncio.sleep(0.05)
                    continue
                if mission_state["failed_step"] is not None:
                    break
                if mission_state["touch_contact_count"] >= 1:
                    timeline = mission_state["timeline"]
                    for timeline_event in timeline:
                        if timeline_event["event"] != "CONTACT":
                            continue
                        force_value = timeline_event["payload"].get("force")
                        if isinstance(force_value, int | float):
                            contact_forces.append(float(force_value))
                    break
                await asyncio.sleep(0.05)
    finally:
        await runtime.stop()

    summary = {
        "trials": trials,
        "samples": len(contact_forces),
        "max_force": max(contact_forces) if contact_forces else None,
        "mean_force": mean(contact_forces) if contact_forces else None,
        "recommended_conservative_limit": (max(contact_forces) * 1.25) if contact_forces else None,
    }
    return summary


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Calibrate touch contact forces in mock simulation"
    )
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/calibration/contact_profile_mock.json"),
    )
    return parser.parse_args()


def main() -> None:
    arguments = parse_arguments()
    summary = asyncio.run(run_calibration(trials=arguments.trials))
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
