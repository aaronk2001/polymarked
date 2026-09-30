from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Sequence
from typing import Any


def detect_arb_bot(trades: Sequence[dict[str, Any]]) -> bool:
    """At least 80% of trades have a same-side hedge on the opposite outcome of the same event within 60s."""
    by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        if t.get("type") != "TRADE":
            continue
        ev = t.get("eventSlug") or t.get("conditionId") or ""
        by_event[ev].append(t)
    total = sum(len(v) for v in by_event.values())
    if total < 10:
        return False
    hedged = 0
    for evs in by_event.values():
        evs.sort(key=lambda t: int(t.get("timestamp", 0)))
        for i, t in enumerate(evs):
            ts = int(t.get("timestamp", 0))
            side = (t.get("side") or "").upper()
            asset = t.get("asset")
            for j in range(i + 1, len(evs)):
                u = evs[j]
                if int(u.get("timestamp", 0)) - ts > 60:
                    break
                if u.get("asset") != asset and (u.get("side") or "").upper() == side:
                    hedged += 1
                    break
    return hedged / total >= 0.80


def detect_single_trade_survivor(realized: Sequence[float]) -> bool:
    if not realized:
        return False
    total = sum(realized)
    if total <= 0:
        return False
    return max(realized) > 0.5 * total


def detect_recency_drought(trades: Sequence[dict[str, Any]], now_ts: int | None = None) -> bool:
    if not trades:
        return True
    last = max(int(t.get("timestamp", 0)) for t in trades)
    if now_ts is None:
        now_ts = int(time.time())
    return (now_ts - last) > 30 * 86400


def detect_low_effective_volume(trades: Sequence[dict[str, Any]]) -> bool:
    eff = 0.0
    for t in trades:
        if t.get("type") != "TRADE":
            continue
        usdc = float(t.get("usdcSize") or 0.0)
        price = float(t.get("price") or 0.0)
        eff += usdc * min(price, 1.0 - price)
    return eff < 500.0
