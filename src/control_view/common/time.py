from __future__ import annotations

import time
from datetime import UTC, datetime


def monotonic_ns() -> int:
    return time.monotonic_ns()


def wall_time_iso() -> str:
    return datetime.now(tz=UTC).isoformat()

