"""Per-wallet activity puller. Cursor-paginated by descending timestamp."""
from __future__ import annotations

from typing import Any

import structlog
from polymarket_agent_core.http import PolymarketHttpClient

log = structlog.get_logger(__name__)

ACTIVITY_PAGE_LIMIT = 500


async def fetch_wallet_activity(
    client: PolymarketHttpClient,
    *,
    wallet: str,
    event_type: str | None = "TRADE",
    start_ts: int | None = None,
    end_ts: int | None = None,
    limit: int = ACTIVITY_PAGE_LIMIT,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"user": wallet, "limit": limit}
    if event_type:
        params["type"] = event_type
    if start_ts is not None:
        params["start"] = start_ts
    if end_ts is not None:
        params["end"] = end_ts
    data = await client.get_json("/activity", params=params)
    return data if isinstance(data, list) else []


async def fetch_all_wallet_activity(
    client: PolymarketHttpClient,
    *,
    wallet: str,
    event_type: str | None = "TRADE",
    start_ts: int | None = None,
    end_ts: int | None = None,
    max_rows: int = 5000,
) -> list[dict[str, Any]]:
    """Cursor-paginate via descending `end` timestamp until exhausted or max_rows hit.

    Polymarket /activity returns newest-first. We advance `end` to (oldest_in_page - 1)
    and dedup on (transactionHash, asset, side) to absorb timestamp ties.
    """
    out: list[dict[str, Any]] = []
    cursor_end = end_ts
    seen: set[tuple[str | None, str | None, str | None]] = set()
    while len(out) < max_rows:
        page = await fetch_wallet_activity(
            client,
            wallet=wallet,
            event_type=event_type,
            start_ts=start_ts,
            end_ts=cursor_end,
            limit=ACTIVITY_PAGE_LIMIT,
        )
        if not page:
            break
        added = 0
        for ev in page:
            key = (ev.get("transactionHash"), ev.get("asset"), ev.get("side"))
            if key in seen:
                continue
            seen.add(key)
            out.append(ev)
            added += 1
            if len(out) >= max_rows:
                break
        if added == 0:
            break
        oldest = min(int(ev["timestamp"]) for ev in page)
        new_cursor = oldest - 1
        if cursor_end is not None and new_cursor >= cursor_end:
            break
        cursor_end = new_cursor
        if len(page) < ACTIVITY_PAGE_LIMIT:
            break
    log.info("activity.fetched", wallet=wallet, count=len(out))
    return out
