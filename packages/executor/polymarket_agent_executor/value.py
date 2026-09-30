"""Favorite-longshot "value" strategy: buy the token priced in the favorite band.

The unbiased calibration (scripts/calibration_market.py) showed Polymarket tokens
priced ~0.55-0.85 resolve YES *more* often than their price, and longshots
(~0.10-0.35) resolve *less* often. Buying the favorite side IS fading the
overpriced longshot side. This scans open markets and opens matching paper
positions into a dedicated book (VALUE_CHAT_ID), reusing the same fill/settle/
mark-to-market machinery as the copy book so the two can be compared head to head.

Writes PaperFill rows directly (no wallet, no TradeDetected) — same shape as
copy_positions.copy_trader_positions.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime

import httpx
import structlog
from polymarket_agent_core import runtime_config as rc
from polymarket_agent_core.config import load_settings
from polymarket_agent_core.db import session_scope
from polymarket_agent_core.models import PaperFill

from .marks import fetch_marks
from .paper import held_asset_ids, paper_cash
from .spreads import fetch_spreads
from .system_state import is_globally_paused

log = structlog.get_logger(__name__)

VALUE_CHAT_ID = -1  # synthetic book id for the value strategy (real Telegram ids are large)


@dataclass(frozen=True)
class ValueScanResult:
    scanned: int
    opened: int
    skipped: int
    invested: float
    cash_left: float
    opened_bets: list[dict] = field(default_factory=list)


def _f(x, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _hours_to_end(end_date: str | None) -> float | None:
    if not end_date:
        return None
    try:
        end = datetime.fromisoformat(end_date.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None
    return (end - time.time()) / 3600.0


async def _open_markets(limit: int) -> list[dict]:
    s = load_settings()
    async with httpx.AsyncClient(timeout=15.0) as c:
        r = await c.get(s.gamma_host + "/markets", params={
            "closed": "false", "active": "true",
            "order": "volume24hr", "ascending": "false", "limit": min(500, max(1, limit))})
        if r.status_code != 200:
            return []
        return r.json() or []


@dataclass(frozen=True)
class _Candidate:
    token: str
    title: str | None      # market question + the side we're buying (e.g. "... — No")
    slug: str | None
    event_slug: str | None
    condition_id: str | None
    gamma_price: float


def _candidates(markets: list[dict], lo: float, hi: float,
                min_liq: float, min_vol: float, min_hours: float) -> list[_Candidate]:
    """One in-band token per qualifying binary order-book market (gamma prefilter).
    The label records WHICH side is bought — a longshot 'Yes' at 0.15 is faded by
    buying the favored 'No' at 0.85, so the title must name the side to avoid
    looking like a bet on the unlikely outcome."""
    out: list[_Candidate] = []
    for m in markets:
        if not m.get("enableOrderBook"):
            continue
        try:
            toks = json.loads(m.get("clobTokenIds") or "[]")
            prices = json.loads(m.get("outcomePrices") or "[]")
            outcomes = json.loads(m.get("outcomes") or "[]")
        except (json.JSONDecodeError, TypeError):
            continue
        if len(toks) != 2 or len(prices) != 2:
            continue
        if _f(m.get("liquidityNum") or m.get("liquidity")) < min_liq:
            continue
        if _f(m.get("volume24hr")) < min_vol:
            continue
        h = _hours_to_end(m.get("endDate"))
        if h is not None and h < min_hours:
            continue
        q = m.get("question")
        for i in (0, 1):
            p = _f(prices[i])
            if lo <= p <= hi and toks[i]:
                side = outcomes[i] if i < len(outcomes) else None
                out.append(_Candidate(
                    token=str(toks[i]),
                    title=f"{q} — {side}" if (q and side) else q,
                    slug=m.get("slug"),
                    event_slug=m.get("slug"),
                    condition_id=m.get("conditionId"),
                    gamma_price=p,
                ))
                break  # at most one in-band side per market
    return out


async def scan_value_markets(*, commit: bool) -> ValueScanResult:
    """Scan top-volume open markets, buy in-band tokens that pass the live mid +
    spread + sizing gates into the value book. commit=False is a dry run (preview)."""
    s = load_settings()
    lo = await rc.get_float("value_buy_low", s.value_buy_low)
    hi = await rc.get_float("value_buy_high", s.value_buy_high)
    min_liq = await rc.get_float("value_min_liquidity_usd", s.value_min_liquidity_usd)
    min_vol = await rc.get_float("value_min_volume24_usd", s.value_min_volume24_usd)
    max_spread = await rc.get_float("value_max_spread", s.value_max_spread)
    order_usd = await rc.get_float("value_order_usd", s.value_order_usd)
    max_open = await rc.get_int("value_max_open", s.value_max_open)
    scan_limit = await rc.get_int("value_scan_limit", s.value_scan_limit)
    min_hours = await rc.get_float("value_min_hours_to_end", s.value_min_hours_to_end)
    slip = await rc.get_float("slippage_bps", s.slippage_bps) / 10000.0
    cap_pct = await rc.get_float("max_position_pct", s.max_position_pct)
    min_order = await rc.get_float("min_order_usd", s.min_order_usd)
    bankroll = s.paper_starting_bankroll_usd

    markets = await _open_markets(scan_limit)
    cands = _candidates(markets, lo, hi, min_liq, min_vol, min_hours)
    if not cands:
        cash = await paper_cash(VALUE_CHAT_ID)
        return ValueScanResult(len(markets), 0, 0, 0.0, cash)

    held = await held_asset_ids(VALUE_CHAT_ID)
    cash = await paper_cash(VALUE_CHAT_ID)
    paused = await is_globally_paused()
    open_count = len(held)

    fresh = [c for c in cands if c.token not in held]
    tokens = list({c.token for c in fresh})
    marks = await fetch_marks(tokens)
    quotes = await fetch_spreads(tokens)

    now = int(time.time())
    opened = skipped = 0
    invested = 0.0
    fills: list[PaperFill] = []
    bets: list[dict] = []
    seen: set[str] = set()
    for c in fresh:
        if c.token in seen:
            continue
        seen.add(c.token)
        mid = marks.get(c.token)
        quote = quotes.get(c.token)
        if mid is None or not (lo <= mid <= hi) or quote is None:
            skipped += 1
            continue
        bid, ask = quote
        if ask - bid > max_spread:
            skipped += 1
            continue
        if paused or open_count >= max_open:
            skipped += 1
            continue
        fill_px = min(0.99, ask * (1 + slip))
        usd = order_usd
        if cap_pct > 0:
            usd = min(usd, cap_pct / 100.0 * bankroll)
        usd = min(usd, cash)
        if usd < min_order:
            skipped += 1
            continue
        fills.append(PaperFill(
            chat_id=VALUE_CHAT_ID,
            target_wallet="VALUE",
            target_tx_hash=f"value:{c.token}:{now}",
            asset=c.token,
            side="BUY",
            size=usd / fill_px,
            price=fill_px,
            usdc_size=usd,
            condition_id=c.condition_id,
            title=c.title,
            slug=c.slug,
            event_slug=c.event_slug,
            timestamp=now,
        ))
        cash -= usd
        invested += usd
        opened += 1
        open_count += 1
        bets.append({"token": c.token, "title": c.title, "price": round(fill_px, 4),
                     "spread": round(ask - bid, 4), "usd": round(usd, 2)})

    if commit and fills:
        async with session_scope() as session:
            session.add_all(fills)

    log.info("value_scan.done", scanned=len(markets), candidates=len(cands),
             opened=opened, skipped=skipped, invested=round(invested, 2), commit=commit)
    return ValueScanResult(len(markets), opened, skipped, invested, cash, bets)
