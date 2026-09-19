from __future__ import annotations

from time import monotonic_ns
from uuid import uuid4


def monotonic_time_ns() -> int:
    return monotonic_ns()


def new_identifier(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"
