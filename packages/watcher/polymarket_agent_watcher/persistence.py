"""Activity-event persistence helpers (SQLite INSERT OR IGNORE on the composite PK)."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from polymarket_agent_core.db import session_scope
from polymarket_agent_core.models import ActivityEvent
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert


def _row_from_raw(wallet: str, raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "proxy_wallet": wallet,
        "transaction_hash": str(raw.get("transactionHash") or ""),
        "asset": str(raw.get("asset") or ""),
        "side": str(raw.get("side") or ""),
        "timestamp": int(raw.get("timestamp") or 0),
        "event_type": str(raw.get("type") or "UNKNOWN"),
        "condition_id": raw.get("conditionId") or None,
        "size": _to_float_or_none(raw.get("size")),
        "usdc_size": _to_float_or_none(raw.get("usdcSize")),
        "price": _to_float_or_none(raw.get("price")),
        "title": raw.get("title") or None,
        "slug": raw.get("slug") or None,
        "event_slug": raw.get("eventSlug") or None,
        "outcome": raw.get("outcome") or None,
        "outcome_index": _to_int_or_none(raw.get("outcomeIndex")),
        "raw": raw,
    }


def _to_float_or_none(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _to_int_or_none(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


_INSERT_CHUNK = 50  # 50 * 17 cols = 850 SQL vars, safe under SQLite default 999


async def persist_activity(wallet: str, events: Sequence[dict[str, Any]]) -> int:
    """INSERT OR IGNORE into activity_event in chunks (SQLite max var safety)."""
    if not events:
        return 0
    rows = [_row_from_raw(wallet, e) for e in events if e.get("transactionHash")]
    if not rows:
        return 0
    async with session_scope() as session:
        for i in range(0, len(rows), _INSERT_CHUNK):
            batch = rows[i:i + _INSERT_CHUNK]
            stmt = sqlite_insert(ActivityEvent).values(batch).on_conflict_do_nothing(
                index_elements=["proxy_wallet", "transaction_hash", "asset", "side"],
            )
            await session.execute(stmt)
    return len(rows)


async def latest_seen_ts(wallet: str) -> int | None:
    """Return the max activity_event.timestamp for this wallet, or None if none."""
    async with session_scope() as session:
        stmt = select(ActivityEvent.timestamp).where(
            ActivityEvent.proxy_wallet == wallet
        ).order_by(ActivityEvent.timestamp.desc()).limit(1)
        result = await session.execute(stmt)
        row = result.scalar_one_or_none()
        return int(row) if row is not None else None
