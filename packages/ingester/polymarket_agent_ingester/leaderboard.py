from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import structlog
from polymarket_agent_core.db import session_scope
from polymarket_agent_core.http import PolymarketHttpClient
from polymarket_agent_core.models import LeaderboardSnapshot, Wallet
from polymarket_agent_core.schemas import LeaderboardEntry
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

log = structlog.get_logger(__name__)

CATEGORIES = (
    "OVERALL", "POLITICS", "SPORTS", "CRYPTO", "CULTURE",
    "MENTIONS", "WEATHER", "ECONOMICS", "TECH", "FINANCE",
)
TIME_PERIODS = ("DAY", "WEEK", "MONTH", "ALL")
ORDER_BYS = ("PNL", "VOL")

LEADERBOARD_PAGE = 50
LEADERBOARD_MAX_OFFSET = 1000
INTER_PAGE_DELAY_SEC = 0.25


async def fetch_leaderboard_slice(
    client: PolymarketHttpClient,
    *,
    category: str,
    time_period: str,
    order_by: str,
    top_n: int = 500,
) -> list[dict[str, Any]]:
    """Paginate the leaderboard endpoint up to top_n rows or the API's hard cap."""
    rows: list[dict[str, Any]] = []
    offset = 0
    while offset < top_n and offset <= LEADERBOARD_MAX_OFFSET:
        limit = min(LEADERBOARD_PAGE, top_n - offset)
        page = await client.get_json(
            "/v1/leaderboard",
            params={
                "category": category,
                "timePeriod": time_period,
                "orderBy": order_by,
                "limit": limit,
                "offset": offset,
            },
        )
        if not isinstance(page, list):
            log.error(
                "leaderboard.unexpected_shape",
                category=category,
                page_type=type(page).__name__,
            )
            break
        if not page:
            break
        rows.extend(page)
        if len(page) < limit:
            break
        offset += limit
        await asyncio.sleep(INTER_PAGE_DELAY_SEC)
    return rows


def _parse_entry(raw: dict[str, Any], rank: int) -> LeaderboardEntry | None:
    try:
        entry = LeaderboardEntry.model_validate(raw)
    except Exception as e:
        log.warning("leaderboard.skip_row", error=str(e), raw_keys=list(raw.keys()))
        return None
    return entry.model_copy(update={"rank": rank + 1})


async def snapshot_leaderboard(
    *,
    category: str = "OVERALL",
    time_period: str = "ALL",
    order_by: str = "PNL",
    top_n: int = 500,
) -> int:
    """Fetch one slice and write a snapshot row + upsert wallet rows. Returns row count."""
    snapshot_ts = datetime.now(UTC)
    async with PolymarketHttpClient() as client:
        rows = await fetch_leaderboard_slice(
            client,
            category=category,
            time_period=time_period,
            order_by=order_by,
            top_n=top_n,
        )

    inserted = 0
    async with session_scope() as session:
        for i, raw in enumerate(rows):
            entry = _parse_entry(raw, i)
            if entry is None:
                continue

            session.add(LeaderboardSnapshot(
                snapshot_ts=snapshot_ts,
                proxy_wallet=entry.proxy_wallet,
                category=category,
                time_period=time_period,
                order_by=order_by,
                rank=entry.rank,
                user_name=entry.user_name,
                vol=entry.vol,
                pnl=entry.pnl,
                x_username=entry.x_username,
                verified_badge=entry.verified_badge,
                profile_image=entry.profile_image,
            ))

            stmt = sqlite_insert(Wallet).values(
                proxy_wallet=entry.proxy_wallet,
                user_name=entry.user_name,
                pseudonym=entry.pseudonym,
                x_username=entry.x_username,
                profile_image=entry.profile_image,
            ).on_conflict_do_update(
                index_elements=["proxy_wallet"],
                set_={
                    "user_name": entry.user_name,
                    "x_username": entry.x_username,
                    "profile_image": entry.profile_image,
                },
            )
            await session.execute(stmt)
            inserted += 1

    log.info(
        "leaderboard.snapshot_written",
        category=category,
        time_period=time_period,
        order_by=order_by,
        count=inserted,
        snapshot_ts=snapshot_ts.isoformat(),
    )
    return inserted
