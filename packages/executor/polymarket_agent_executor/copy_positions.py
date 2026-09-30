"""Copy a followed trader's CURRENT open positions into the paper book.

Unlike the watcher (which mirrors future trades), this snapshots what the trader
holds right now via Polymarket's data-api /positions and opens matching paper
positions, sized by the follow's copy_ratio and capped by max_per_trade + cash.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
import structlog
from polymarket_agent_core import runtime_config as rc
from polymarket_agent_core.config import load_settings
from polymarket_agent_core.db import session_scope
from polymarket_agent_core.models import Follow, PaperFill
from sqlalchemy import select

from .paper import held_asset_ids, paper_cash

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class CopyResult:
    copied: int
    skipped: int
    invested: float
    cash_left: float


async def _fetch_positions(wallet: str) -> list[dict]:
    s = load_settings()
    base = getattr(s, "data_api_host", "https://data-api.polymarket.com")
    async with httpx.AsyncClient(base_url=base, timeout=20.0) as c:
        r = await c.get("/positions", params={
            "user": wallet, "limit": 500, "sortBy": "CURRENT", "sortDirection": "DESC",
        })
        if r.status_code != 200:
            return []
        d = r.json()
        return d if isinstance(d, list) else []


async def copy_trader_positions(chat_id: int, wallet: str) -> CopyResult:
    """Open paper positions mirroring the trader's current holdings. Skips assets
    already held (idempotent re-copy), positions priced at 0/1 (resolved), and
    anything below the min order or beyond available cash."""
    wallet = wallet.lower()
    async with session_scope() as session:
        follow = (await session.execute(
            select(Follow).where(Follow.chat_id == chat_id, Follow.proxy_wallet == wallet)
        )).scalar_one_or_none()
    if follow is None:
        return CopyResult(0, 0, 0.0, 0.0)

    positions = await _fetch_positions(wallet)
    cash = await paper_cash(chat_id)
    held = await held_asset_ids(chat_id)
    s = load_settings()
    min_order = await rc.get_float("min_order_usd", s.min_order_usd)
    min_value = await rc.get_float("copy_min_value_usd", s.copy_min_value_usd)
    lo = await rc.get_float("copy_skip_price_low", s.copy_skip_price_low)
    hi = await rc.get_float("copy_skip_price_high", s.copy_skip_price_high)
    slip = await rc.get_float("slippage_bps", s.slippage_bps) / 10000.0

    now = int(time.time())
    copied = skipped = 0
    invested = 0.0
    fills: list[PaperFill] = []
    for p in positions:
        asset = p.get("asset")
        size = float(p.get("size") or 0.0)
        cur = float(p.get("curPrice") or 0.0)
        cur_value = float(p.get("currentValue") or 0.0)
        # skip: no asset, already held, resolved/near-resolved, or the target's dust
        if (not asset or size <= 0 or cur <= lo or cur >= hi
                or asset in held or cur_value < min_value):
            skipped += 1
            continue
        cur = min(0.99, cur * (1 + slip))  # buy in slightly worse (realism)
        usd = min(cur_value * follow.copy_ratio, follow.max_per_trade_usd)
        if usd < min_order or usd > cash:
            skipped += 1
            continue
        fills.append(PaperFill(
            chat_id=chat_id,
            target_wallet=wallet,
            target_tx_hash=f"copypos:{asset}:{now}",
            asset=asset,
            side="BUY",
            size=usd / cur,
            price=cur,
            usdc_size=usd,
            condition_id=p.get("conditionId"),
            title=p.get("title"),
            slug=p.get("slug"),
            event_slug=p.get("eventSlug"),
            timestamp=now,
        ))
        cash -= usd
        invested += usd
        copied += 1

    if fills:
        async with session_scope() as session:
            session.add_all(fills)
    log.info("copy_positions.done", wallet=wallet, copied=copied, skipped=skipped,
             invested=round(invested, 2))
    return CopyResult(copied, skipped, invested, cash)
