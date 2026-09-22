from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

import yaml

from flybot_api.service import FlyBotRuntime
from flybot_executive.progress import classify_mission_progress


def _load_scenario_files(scenarios_directory: Path) -> list[dict[str, Any]]:
    scenario_files = sorted(scenarios_directory.glob("demo_d*.yaml"))
    scenarios: list[dict[str, Any]] = []
    for scenario_file in scenario_files:
        scenario_payload = yaml.safe_load(scenario_file.read_text(encoding="utf-8"))
        scenario_payload["source_file"] = str(scenario_file)
        scenarios.append(scenario_payload)
    return scenarios


def _parse_seed_list(seeds_argument: str) -> list[int]:
    return [int(seed_text.strip()) for seed_text in seeds_argument.split(",") if seed_text.strip()]


async def _run_single_benchmark(
    runtime: FlyBotRuntime, scenario: dict[str, Any], seed: int, timeout_s: float
) -> dict[str, Any]:
    await runtime.reset(seed=seed)
    instruction = str(scenario["instruction"])
    benchmark_started_at = time.perf_counter()
    mission_id = await runtime.submit_instruction(instruction)
    while True:
        mission_state = runtime.status().mission_state
        if mission_state["mission_id"] != mission_id:
            await asyncio.sleep(0.05)
            continue
        progress = classify_mission_progress(mission_state)
        if progress == "failed":
            elapsed_s = time.perf_counter() - benchmark_started_at
            return {
                "scenario": scenario["name"],
                "seed": seed,
                "instruction": instruction,
                "success": False,
                "duration_s": elapsed_s,
                "failed_step": mission_state["failed_step"],
                "completed_steps": len(mission_state["completed_steps"]),
            }
        if progress == "succeeded":
            elapsed_s = time.perf_counter() - benchmark_started_at
            return {
                "scenario": scenario["name"],
                "seed": seed,
                "instruction": instruction,
                "success": True,
                "duration_s": elapsed_s,
                "failed_step": None,
                "completed_steps": len(mission_state["completed_steps"]),
                "change_events_count": mission_state["change_events_count"],
                "follow_min_distance_observed": mission_state["follow_min_distance_observed"],
                "touch_contact_count": mission_state["touch_contact_count"],
            }
        elapsed_s = time.perf_counter() - benchmark_started_at
        if elapsed_s >= timeout_s:
            return {
                "scenario": scenario["name"],
                "seed": seed,
                "instruction": instruction,
                "success": False,
                "duration_s": elapsed_s,
                "failed_step": {"reason": "BENCHMARK_TIMEOUT"},
                "completed_steps": len(mission_state["completed_steps"]),
            }
        await asyncio.sleep(0.05)


async def run_benchmark(
    scenarios: list[dict[str, Any]],
    seeds: list[int],
    timeout_s: float,
) -> list[dict[str, Any]]:
    runtime = FlyBotRuntime()
    await runtime.start()
    results: list[dict[str, Any]] = []
    try:
        for seed in seeds:
            for scenario in scenarios:
                result = await _run_single_benchmark(runtime, scenario, seed, timeout_s)
                print(
                    f"[benchmark] scenario={result['scenario']} seed={seed} "
                    f"success={result['success']} duration_s={result['duration_s']:.2f}"
                )
                results.append(result)
    finally:
        await runtime.stop()
    return results


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="FlyBot M9 benchmark scaffold with seed sweeps")
    parser.add_argument("--scenarios-dir", type=Path, default=Path("scenarios"))
    parser.add_argument("--seeds", type=str, default="42,43,44")
    parser.add_argument("--timeout-s", type=float, default=30.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/benchmark/seed_sweep_summary.json"),
    )
    return parser.parse_args()


def main() -> None:
    arguments = parse_arguments()
    scenarios = _load_scenario_files(arguments.scenarios_dir)
    if not scenarios:
        raise SystemExit("No benchmark scenarios found. Expected files like scenarios/demo_d*.yaml")
    seeds = _parse_seed_list(arguments.seeds)
    results = asyncio.run(
        run_benchmark(
            scenarios=scenarios,
            seeds=seeds,
            timeout_s=arguments.timeout_s,
        )
    )
    summary = {
        "generated_at_ns": time.time_ns(),
        "seeds": seeds,
        "scenario_count": len(scenarios),
        "result_count": len(results),
        "success_count": sum(1 for result in results if result["success"]),
        "results": results,
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Wrote benchmark summary to {arguments.output}")
    if summary["success_count"] != summary["result_count"]:
        raise SystemExit("One or more benchmark runs failed")


if __name__ == "__main__":
    main()
