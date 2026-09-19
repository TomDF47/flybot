from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from time import time_ns
from typing import Any


@dataclass
class TelemetryRecorder:
    output_path: Path | None = None
    _events: list[dict[str, Any]] = field(default_factory=list)

    def emit(self, event_name: str, payload: dict[str, Any]) -> None:
        event = {"event": event_name, "timestamp_ns": time_ns(), "payload": payload}
        self._events.append(event)
        if self.output_path is not None:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            with self.output_path.open("a", encoding="utf-8") as output_file:
                output_file.write(json.dumps(event))
                output_file.write("\n")

    @property
    def events(self) -> list[dict[str, Any]]:
        return self._events
