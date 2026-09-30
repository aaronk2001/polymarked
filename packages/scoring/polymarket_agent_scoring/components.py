from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Component:
    raw: float
    normalized: float


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b else default


def realized_pnl_per_trade(events: Sequence[dict[str, Any]]) -> list[float]:
    """Realized PnL series across TRADE + REDEEM + REWARD events.

    - TRADE BUY accumulates weighted-avg cost basis per asset.
    - TRADE SELL realizes (sell_price - avg_cost) * close_size.
    - REDEEM zeros all positions under that conditionId and realizes
      (usdcSize - sum_of_remaining_cost_basis). This is what the official
      leaderboard's naive PnL misses for whales who never SELL.
    - REWARD is added directly as positive realized PnL.
    - SPLIT/MERGE are PnL-neutral and ignored at Phase 1.
    """
    positions: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    cond_to_assets: dict[str, set[str]] = defaultdict(set)
    realized: list[float] = []
    for e in sorted(events, key=lambda x: int(x.get("timestamp", 0))):
        etype = e.get("type")
        if etype == "TRADE":
            side = (e.get("side") or "").upper()
            size = float(e.get("size") or 0.0)
            usdc = float(e.get("usdcSize") or 0.0)
            asset = e.get("asset") or ""
            cond = e.get("conditionId") or ""
            if size <= 0 or usdc <= 0 or not asset:
                continue
            if cond:
                cond_to_assets[cond].add(asset)
            pos = positions[asset]
            if side == "BUY":
                pos[0] += size
                pos[1] += usdc
            elif side == "SELL":
                avg_cost = _safe_div(pos[1], pos[0])
                close_size = min(size, pos[0])
                if close_size > 0:
                    realized.append((usdc / size - avg_cost) * close_size)
                    pos[0] -= close_size
                    pos[1] -= avg_cost * close_size
        elif etype == "REDEEM":
            cond = e.get("conditionId") or ""
            proceeds = float(e.get("usdcSize") or 0.0)
            cost_basis = 0.0
            for asset_id in cond_to_assets.get(cond, ()):
                pos = positions[asset_id]
                cost_basis += pos[1]
                pos[0] = 0.0
                pos[1] = 0.0
            realized.append(proceeds - cost_basis)
        elif etype == "REWARD":
            realized.append(float(e.get("usdcSize") or 0.0))
    return realized


def profit_factor(realized: Sequence[float]) -> Component:
    pos = sum(p for p in realized if p > 0)
    neg = abs(sum(p for p in realized if p < 0))
    raw = (10.0 if pos > 0 else 0.0) if neg == 0 else pos / neg
    normalized = 100.0 * raw / (raw + 1.0) if raw >= 0 else 0.0
    return Component(raw=raw, normalized=normalized)


def sharpe_like(realized: Sequence[float]) -> Component:
    n = len(realized)
    if n < 2:
        return Component(raw=0.0, normalized=30.0)
    mean = sum(realized) / n
    var = sum((x - mean) ** 2 for x in realized) / (n - 1)
    sd = math.sqrt(var)
    raw = (5.0 if mean > 0 else 0.0) if sd == 0 else mean / sd
    normalized = max(0.0, min(100.0, 50.0 + raw * 30.0))
    return Component(raw=raw, normalized=normalized)


def win_rate(realized: Sequence[float]) -> Component:
    n = len(realized)
    if n == 0:
        return Component(raw=0.0, normalized=0.0)
    wins = sum(1 for x in realized if x > 0)
    raw = wins / n
    confidence = min(1.0, math.sqrt(n) / math.sqrt(50.0))
    normalized = 100.0 * raw * confidence + 50.0 * (1.0 - confidence)
    return Component(raw=raw, normalized=normalized)


def max_drawdown(realized: Sequence[float]) -> Component:
    if not realized:
        return Component(raw=0.0, normalized=50.0)
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for x in realized:
        cum += x
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    raw = _safe_div(max_dd, peak, default=0.0)
    normalized = 100.0 * (1.0 - min(1.0, raw))
    return Component(raw=raw, normalized=normalized)


def trade_count_score(trades: Sequence[dict[str, Any]]) -> Component:
    n = sum(1 for t in trades if t.get("type") == "TRADE")
    raw = float(n)
    normalized = min(100.0, 100.0 * math.log10(max(1, n)) / 3.0)
    return Component(raw=raw, normalized=normalized)


def calibration_error(trades: Sequence[dict[str, Any]]) -> Component:
    """Phase 1 stub: requires market resolution data not yet ingested. Returns neutral 50."""
    return Component(raw=0.0, normalized=50.0)
