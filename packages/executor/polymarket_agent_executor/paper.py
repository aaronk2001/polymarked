"""Paper-trading: virtual fills + balance computation reusing Smart Score PnL.

Open positions are marked-to-market against live Polymarket midpoint prices
(marks.py); if a live mark is unavailable the last fill price is used as a
fallback so the book always values.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from polymarket_agent_core import runtime_config as rc
from polymarket_agent_core.config import load_settings
from polymarket_agent_core.db import session_scope
from polymarket_agent_core.models import PaperFill
from polymarket_agent_watcher import TradeDetected
from sqlalchemy import select

from .marks import fetch_marks
from .resolutions import fetch_resolutions

_EPS = 1e-9


@dataclass(frozen=True)
class PaperPosition:
    asset: str
    title: str | None
    event_slug: str | None
    condition_id: str | None
    size: float
    avg_entry: float
    last_fill_price: float
    mark: float
    marked_live: bool
    price_source: str  # "live" | "resolved" | "cost"
    cost_basis: float
    value: float
    unrealized_pnl: float
    unrealized_pct: float


@dataclass(frozen=True)
class PaperBalance:
    fills: int
    realized_pnl: float
    unrealized_pnl: float
    open_positions: int
    starting_bankroll: float
    cash: float
    position_value: float
    total: float
    marked_live: bool


@dataclass
class _Lot:
    size: float = 0.0
    cost: float = 0.0
    last_price: float = 0.0
    title: str | None = None
    event_slug: str | None = None
    condition_id: str | None = None


async def write_paper_fill(
    *, chat_id: int, ev: TradeDetected, intended_usdc: float, fill_price: float | None = None
) -> str:
    """Write a virtual fill at the target's fill price (default) and return the fill_id."""
    price = fill_price if fill_price is not None else ev.price
    if price <= 0:
        price = max(ev.price, 0.01)
    size = intended_usdc / price if price > 0 else 0.0
    fill = PaperFill(
        chat_id=chat_id,
        target_wallet=ev.wallet,
        target_tx_hash=ev.transaction_hash,
        asset=ev.asset,
        side=ev.side,
        size=size,
        price=price,
        usdc_size=intended_usdc,
        condition_id=(ev.raw.get("conditionId") if ev.raw else None),
        title=ev.title,
        slug=ev.slug,
        event_slug=ev.event_slug,
        timestamp=ev.timestamp or int(time.time()),
    )
    async with session_scope() as session:
        session.add(fill)
        await session.flush()
        return fill.fill_id


def _walk_fills(rows: list[PaperFill]) -> tuple[float, dict[str, _Lot], float]:
    """Replay fills into cash + per-asset weighted-average-cost lots + realized PnL.
    Realized is tracked here (cash-consistent) so realized + unrealized == total -
    starting exactly — unlike the scoring `realized_pnl_per_trade`, which uses a
    different cost model and won't reconcile with the book."""
    starting = load_settings().paper_starting_bankroll_usd
    cash = starting
    realized = 0.0
    lots: dict[str, _Lot] = {}
    for r in rows:
        lot = lots.setdefault(r.asset, _Lot())
        side = (r.side or "").upper()
        if side == "BUY":
            cash -= r.usdc_size
            lot.size += r.size
            lot.cost += r.usdc_size
        elif side == "SELL":
            avg = lot.cost / lot.size if lot.size > _EPS else 0.0
            close = min(r.size, lot.size)  # can't sell more than we hold
            proceeds = (close / r.size) * r.usdc_size if r.size > _EPS else 0.0
            cash += proceeds  # credit only the closed portion (no oversell leak)
            realized += proceeds - avg * close
            lot.size -= close
            lot.cost -= avg * close
        elif side == "REDEEM":  # settlement of a resolved market: cash out, close lot
            cash += r.usdc_size
            realized += r.usdc_size - lot.cost
            lot.size = 0.0
            lot.cost = 0.0
        if r.price > 0:
            lot.last_price = r.price
        if r.title:
            lot.title = r.title
        if r.event_slug:
            lot.event_slug = r.event_slug
        if r.condition_id:
            lot.condition_id = r.condition_id
    return cash, lots, realized


def _to_event(r: PaperFill) -> dict:
    if (r.side or "").upper() == "REDEEM":
        return {
            "type": "REDEEM",
            "usdcSize": r.usdc_size,
            "conditionId": r.condition_id or "",
            "timestamp": r.timestamp,
        }
    return {
        "type": "TRADE",
        "side": r.side,
        "size": r.size,
        "usdcSize": r.usdc_size,
        "asset": r.asset,
        "conditionId": r.condition_id or "",
        "timestamp": r.timestamp,
    }


async def paper_cash(chat_id: int) -> float:
    """Current paper cash (starting bankroll minus net spent). Fast: no marks."""
    async with session_scope() as session:
        rows = list((await session.execute(
            select(PaperFill).where(PaperFill.chat_id == chat_id).order_by(PaperFill.timestamp)
        )).scalars())
    cash, _, _ = _walk_fills(rows)
    return cash


async def held_asset_ids(chat_id: int) -> set[str]:
    """Asset ids currently held with positive net size (to avoid double-buying)."""
    async with session_scope() as session:
        rows = list((await session.execute(
            select(PaperFill).where(PaperFill.chat_id == chat_id).order_by(PaperFill.timestamp)
        )).scalars())
    _, lots, _ = _walk_fills(rows)
    return {a for a, lot in lots.items() if lot.size > _EPS}


async def paper_position_size(chat_id: int, asset: str) -> float:
    """Current net shares held in one asset (0 if none). For mirroring exits."""
    async with session_scope() as session:
        rows = list((await session.execute(
            select(PaperFill).where(PaperFill.chat_id == chat_id).order_by(PaperFill.timestamp)
        )).scalars())
    _, lots, _ = _walk_fills(rows)
    lot = lots.get(asset)
    return lot.size if lot else 0.0


async def paper_snapshot(
    chat_id: int, *, with_marks: bool = True
) -> tuple[PaperBalance, list[PaperPosition]]:
    """Compute the full paper book: balance + per-position mark-to-market detail."""
    async with session_scope() as session:
        rows = list((await session.execute(
            select(PaperFill).where(PaperFill.chat_id == chat_id).order_by(PaperFill.timestamp)
        )).scalars())

    cash, lots, realized = _walk_fills(rows)
    open_assets = [a for a, lot in lots.items() if lot.size > _EPS]
    # Value the largest positions by cost basis: live CLOB midpoint first, then
    # Gamma resolution (0/1) for closed markets. The long tail is held at cost
    # basis (0 unrealized) rather than a stale last-fill price.
    marks: dict[str, float] = {}
    resolutions: dict[str, float] = {}
    if with_marks and open_assets:
        cap = await rc.get_int("mark_max_tokens", load_settings().mark_max_tokens)
        priced = sorted(open_assets, key=lambda a: lots[a].cost, reverse=True)[:cap]
        marks = await fetch_marks(priced)
        unmarked = [a for a in priced if a not in marks]
        if unmarked:
            resolutions = await fetch_resolutions(unmarked)

    # Positions worth less than this (resolved-lost at ~0, or near-zero markers)
    # are still counted in the totals but hidden from the active list/count so
    # the dashboard doesn't show a pile of "$0 bets".
    dust = await rc.get_float("active_dust_usd", load_settings().active_dust_usd)

    positions: list[PaperPosition] = []
    position_value = 0.0
    unrealized = 0.0
    active = 0
    any_live = False
    for asset in open_assets:
        lot = lots[asset]
        avg_entry = lot.cost / lot.size if lot.size > _EPS else 0.0
        live = marks.get(asset)
        resolved = resolutions.get(asset)
        if live is not None:
            mark, source = live, "live"
            any_live = True
        elif resolved is not None:
            mark, source = resolved, "resolved"
        else:
            mark, source = avg_entry, "cost"  # honest: no live price -> 0 unrealized
        value = lot.size * mark
        upnl = value - lot.cost
        position_value += value
        unrealized += upnl
        if value < dust:
            continue  # dust / resolved-lost — keep in totals, hide from active view
        active += 1
        positions.append(PaperPosition(
            asset=asset,
            title=lot.title,
            event_slug=lot.event_slug,
            condition_id=lot.condition_id,
            size=lot.size,
            avg_entry=avg_entry,
            last_fill_price=lot.last_price,
            mark=mark,
            marked_live=source == "live",
            price_source=source,
            cost_basis=lot.cost,
            value=value,
            unrealized_pnl=upnl,
            unrealized_pct=(upnl / lot.cost * 100.0) if lot.cost > _EPS else 0.0,
        ))

    positions.sort(key=lambda p: p.value, reverse=True)
    starting = load_settings().paper_starting_bankroll_usd
    balance = PaperBalance(
        fills=len(rows),
        realized_pnl=realized,
        unrealized_pnl=unrealized,
        open_positions=active,
        starting_bankroll=starting,
        cash=cash,
        position_value=position_value,
        total=cash + position_value,
        marked_live=any_live,
    )
    return balance, positions


async def paper_balance(chat_id: int) -> PaperBalance:
    bal, _ = await paper_snapshot(chat_id)
    return bal


@dataclass(frozen=True)
class SettlementResult:
    settled: int
    proceeds: float
    realized_pnl: float
    won: int
    lost: int
    stopped: int = 0


async def settle_resolved_positions(
    chat_id: int, *, commit: bool, stop_price: float = 0.0
) -> SettlementResult:
    """Close open positions by writing a REDEEM fill at the close price:
    - market resolved -> resolved price (1.0 won / 0.0 lost)
    - stop_price > 0 and live mark <= stop_price -> close at mark (stop-loss)
    Books realized PnL, returns cash, removes the position from 'open'. Idempotent
    (settled lots net to zero). `commit=False` is a dry run.
    """
    async with session_scope() as session:
        rows = list((await session.execute(
            select(PaperFill).where(PaperFill.chat_id == chat_id).order_by(PaperFill.timestamp)
        )).scalars())
    cash, lots, _ = _walk_fills(rows)
    open_assets = [a for a, lot in lots.items() if lot.size > _EPS]
    resolutions = await fetch_resolutions(open_assets) if open_assets else {}

    # Stop-loss: live-mark the not-yet-resolved positions, close any at/under the floor.
    marks: dict[str, float] = {}
    if stop_price > 0:
        unresolved = [a for a in open_assets if a not in resolutions]
        if unresolved:
            marks = await fetch_marks(unresolved)

    now = int(time.time())
    settled = won = lost = stopped = 0
    total_proceeds = total_realized = 0.0
    to_write: list[PaperFill] = []
    for asset in open_assets:
        price = resolutions.get(asset)
        is_stop = False
        if price is None:
            m = marks.get(asset)
            if stop_price > 0 and m is not None and m <= stop_price:
                price, is_stop = m, True
            else:
                continue
        lot = lots[asset]
        proceeds = lot.size * price
        total_proceeds += proceeds
        total_realized += proceeds - lot.cost
        settled += 1
        if is_stop:
            stopped += 1
        elif price >= 0.5:
            won += 1
        else:
            lost += 1
        tag = "stop" if is_stop else "settle"
        to_write.append(PaperFill(
            chat_id=chat_id,
            target_wallet="STOPLOSS" if is_stop else "SETTLEMENT",
            target_tx_hash=f"{tag}:{asset}:{now}",
            asset=asset,
            side="REDEEM",
            size=lot.size,
            price=price,
            usdc_size=proceeds,
            condition_id=lot.condition_id,
            title=lot.title,
            slug=None,
            event_slug=lot.event_slug,
            timestamp=now,
        ))

    if commit and to_write:
        async with session_scope() as session:
            session.add_all(to_write)

    return SettlementResult(
        settled=settled,
        proceeds=total_proceeds,
        realized_pnl=total_realized,
        won=won,
        lost=lost,
        stopped=stopped,
    )
