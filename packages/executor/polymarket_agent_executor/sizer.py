"""Adaptive position sizing for copy-trades. Polymarket has a $1 minimum order."""
from __future__ import annotations

from dataclasses import dataclass

from polymarket_agent_core.models import Follow

MIN_ORDER_USDC = 1.0


@dataclass(frozen=True)
class IntendedTrade:
    size_usdc: float
    skip_reason: str | None = None


def size_trade(target_usdc: float, follow: Follow, min_order: float = MIN_ORDER_USDC) -> IntendedTrade:
    desired = max(0.0, target_usdc) * follow.copy_ratio
    capped = min(desired, follow.max_per_trade_usd)
    if capped < min_order:
        return IntendedTrade(size_usdc=0.0, skip_reason="BELOW_MIN_ORDER")
    return IntendedTrade(size_usdc=capped, skip_reason=None)
