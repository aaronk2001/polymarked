from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from . import components, red_flags
from .components import Component

# calibration_error is a Phase-1 stub (flat 50), so it's excluded from the weighted
# score and the other five are renormalized to sum to 1.0. It's still computed and
# returned in `components` for display, just not weighted, so the score isn't ~15% noise.
WEIGHTS: dict[str, float] = {
    "profit_factor": 0.235,
    "sharpe_like": 0.235,
    "win_rate": 0.118,
    "max_drawdown": 0.235,
    "trade_count": 0.177,
}

RED_FLAG_DEDUCTIONS: dict[str, float] = {
    "arb_bot": -30.0,
    "single_trade_survivor": -20.0,
    "recency_drought": -10.0,
}


@dataclass
class ScoreReport:
    smart_score: float
    tier: str
    components: dict[str, Component]
    red_flags: list[str]
    realized_pnl_total: float
    trade_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "smart_score": self.smart_score,
            "tier": self.tier,
            "red_flags": self.red_flags,
            "realized_pnl_total": self.realized_pnl_total,
            "trade_count": self.trade_count,
            "components": {
                k: {"raw": v.raw, "normalized": v.normalized}
                for k, v in self.components.items()
            },
        }


def _tier(score: float) -> str:
    if score >= 70:
        return "green"
    if score >= 50:
        return "yellow"
    if score >= 30:
        return "orange"
    return "red"


def compute_smart_score(trades: Sequence[dict[str, Any]]) -> ScoreReport:
    realized = components.realized_pnl_per_trade(trades)
    comps = {
        "profit_factor": components.profit_factor(realized),
        "sharpe_like": components.sharpe_like(realized),
        "win_rate": components.win_rate(realized),
        "max_drawdown": components.max_drawdown(realized),
        "trade_count": components.trade_count_score(trades),
        "calibration_error": components.calibration_error(trades),
    }
    base = sum(WEIGHTS[k] * comps[k].normalized for k in WEIGHTS)

    flags: list[str] = []
    if red_flags.detect_arb_bot(trades):
        flags.append("arb_bot")
    if red_flags.detect_single_trade_survivor(realized):
        flags.append("single_trade_survivor")
    if red_flags.detect_recency_drought(trades):
        flags.append("recency_drought")
    cap_at_30 = red_flags.detect_low_effective_volume(trades)
    if cap_at_30:
        flags.append("low_effective_volume")

    final = base + sum(RED_FLAG_DEDUCTIONS.get(f, 0.0) for f in flags)
    if cap_at_30:
        final = min(final, 30.0)
    final = max(0.0, min(100.0, final))

    return ScoreReport(
        smart_score=final,
        tier=_tier(final),
        components=comps,
        red_flags=flags,
        realized_pnl_total=sum(realized),
        trade_count=sum(1 for t in trades if t.get("type") == "TRADE"),
    )
