"""Normalized internal events emitted by the wallet watcher."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TradeDetected:
    wallet: str
    transaction_hash: str
    timestamp: int
    asset: str
    side: str
    size: float
    usdc_size: float
    price: float
    title: str | None
    slug: str | None
    event_slug: str | None
    outcome: str | None
    outcome_index: int | None
    raw: dict[str, Any]

    @classmethod
    def from_raw(cls, wallet: str, raw: dict[str, Any]) -> TradeDetected:
        return cls(
            wallet=wallet,
            transaction_hash=str(raw.get("transactionHash") or ""),
            timestamp=int(raw.get("timestamp") or 0),
            asset=str(raw.get("asset") or ""),
            side=str(raw.get("side") or ""),
            size=float(raw.get("size") or 0.0),
            usdc_size=float(raw.get("usdcSize") or 0.0),
            price=float(raw.get("price") or 0.0),
            title=raw.get("title"),
            slug=raw.get("slug"),
            event_slug=raw.get("eventSlug"),
            outcome=raw.get("outcome"),
            outcome_index=raw.get("outcomeIndex"),
            raw=raw,
        )
