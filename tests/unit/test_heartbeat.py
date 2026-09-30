"""Unit tests for the heartbeat registry."""
from __future__ import annotations

import time

from polymarket_agent_core import heartbeat


def setup_function() -> None:
    heartbeat.reset()


def test_beat_records_timestamp() -> None:
    before = time.time()
    heartbeat.beat("watcher")
    after = time.time()
    ts = heartbeat.last("watcher")
    assert ts is not None
    assert before <= ts <= after


def test_unknown_name_returns_none() -> None:
    assert heartbeat.last("never_beat") is None


def test_snapshot_is_independent_copy() -> None:
    heartbeat.beat("a")
    snap = heartbeat.snapshot()
    heartbeat.beat("b")
    assert "b" not in snap  # snapshot was a copy, not a live ref
    assert "a" in snap


def test_reset_clears_table() -> None:
    heartbeat.beat("x")
    heartbeat.reset()
    assert heartbeat.snapshot() == {}
