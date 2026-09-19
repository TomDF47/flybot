from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay and summarize FlyBot telemetry JSONL")
    parser.add_argument("telemetry_jsonl", type=Path, help="Path to telemetry JSONL file")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/replay/replay_summary.json"),
        help="Output summary JSON path",
    )
    return parser.parse_args()


def load_events(path: Path) -> list[dict[str, Any]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    events: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        events.append(json.loads(line))
    return events


def summarize_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    event_counter: Counter[str] = Counter()
    mission_ids: set[str] = set()
    for event in events:
        event_name = str(event.get("event", "UNKNOWN"))
        event_counter[event_name] += 1
        payload = event.get("payload", {})
        if isinstance(payload, dict):
            mission_id = payload.get("mission_id")
            if isinstance(mission_id, str):
                mission_ids.add(mission_id)
    return {
        "event_count": len(events),
        "event_types": dict(event_counter),
        "mission_ids": sorted(mission_ids),
    }


def main() -> None:
    arguments = parse_arguments()
    events = load_events(arguments.telemetry_jsonl)
    summary = summarize_events(events)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Wrote replay summary to {arguments.output}")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
