"""In-process heartbeat registry for /health endpoint.

Subsystems (watcher, bot, supervisor) call `beat(name)` after each successful
tick. The /health endpoint reads `snapshot()` to surface staleness without
needing IPC. Process-local — restarts reset the table.
"""
from __future__ import annotations

import time
from threading import Lock
from typing import Final

_lock: Final = Lock()
_state: dict[str, float] = {}


def beat(name: str) -> None:
    """Record `name` as having ticked just now (UTC unix seconds)."""
    with _lock:
        _state[name] = time.time()


def last(name: str) -> float | None:
    """Return last-tick timestamp for `name`, or None if it has never beat."""
    with _lock:
        return _state.get(name)


def snapshot() -> dict[str, float]:
    """Return a copy of the full heartbeat table."""
    with _lock:
        return dict(_state)


def reset() -> None:
    """Test helper — clear the table."""
    with _lock:
        _state.clear()
