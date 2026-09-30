"""Orchestrate sizer -> risk -> mode-specific path. Single entry point: record_decision."""
from __future__ import annotations

from dataclasses import dataclass

import structlog
from polymarket_agent_core import runtime_config as rc
from polymarket_agent_core.config import load_settings
from polymarket_agent_core.db import session_scope
from polymarket_agent_core.models import CopyDecision, Follow
from polymarket_agent_watcher import TradeDetected
from sqlalchemy import select

from .paper import paper_cash, paper_position_size, write_paper_fill
from .risk import check_caps
from .sizer import size_trade
from .system_state import get_effective_mode, is_globally_paused

log = structlog.get_logger(__name__)

# Max acceptable slippage between target's fill price and our book best, fraction.
MAX_SLIPPAGE_VS_TARGET = 0.03  # 3%


@dataclass(frozen=True)
class Decision:
    decision: str
    reason: str
    intended_size_usd: float
    intended_price: float
    intended_side: str
    paper_fill_id: str | None = None
    own_tx_hash: str | None = None
    own_filled_size: float | None = None
    own_filled_price: float | None = None


async def _load_follow(chat_id: int, wallet: str) -> Follow | None:
    async with session_scope() as session:
        stmt = select(Follow).where(Follow.chat_id == chat_id, Follow.proxy_wallet == wallet)
        return (await session.execute(stmt)).scalar_one_or_none()


async def _execute_live(ev: TradeDetected, intended_usdc: float, follow: Follow) -> Decision:
    """Live-mode FOK with slippage gate. Writes own_tx_hash on success."""
    from .clob import best_for_side, place_fok

    try:
        best = await best_for_side(ev.asset, ev.side)
    except Exception as e:
        return Decision(
            "FAILED", f"book_lookup_failed: {e}",
            intended_usdc, ev.price, ev.side,
        )
    if best is None:
        return Decision(
            "SKIPPED_RISK", "no liquidity on this side",
            intended_usdc, ev.price, ev.side,
        )
    target_price = ev.price or best
    slippage = abs(best - target_price) / max(target_price, 0.01)
    if slippage > MAX_SLIPPAGE_VS_TARGET:
        return Decision(
            "SKIPPED_RISK",
            f"slippage_vs_target {slippage:.3f} > {MAX_SLIPPAGE_VS_TARGET:.3f}",
            intended_usdc, ev.price, ev.side,
        )
    # Convert intended_usdc to share size at the worst-acceptable price.
    fill_price = best
    size = intended_usdc / max(fill_price, 0.01)
    res = await place_fok(token_id=ev.asset, side=ev.side, size=size, price=fill_price)
    if not res.ok:
        return Decision(
            "FAILED", f"FOK rejected: {res.reason}",
            intended_usdc, fill_price, ev.side,
        )
    tx = res.tx_hashes[0] if res.tx_hashes else None
    return Decision(
        "EXECUTED",
        f"FOK filled {res.filled_size:.2f} @ {res.filled_price:.3f}",
        intended_usdc, fill_price, ev.side,
        own_tx_hash=tx,
        own_filled_size=res.filled_size,
        own_filled_price=res.filled_price,
    )


async def record_decision(ev: TradeDetected) -> Decision:
    """Compute, persist, and return the trade decision for an incoming TradeDetected event."""
    s = load_settings()
    chat_id = s.telegram_owner_chat_id
    mode = await get_effective_mode()
    min_order = await rc.get_float("min_order_usd", s.min_order_usd)
    follow = await _load_follow(chat_id, ev.wallet)

    if follow is None:
        return Decision("NO_FOLLOW", "wallet not followed by owner",
                        0.0, ev.price, ev.side)

    if follow.paused:
        d = Decision("SKIPPED_PAUSED", "follow is muted", 0.0, ev.price, ev.side)
    elif mode == "off":
        intended = size_trade(ev.usdc_size, follow, min_order)
        d = Decision(
            "SKIPPED_DRY_RUN", f"trade_mode=off (would have been ${intended.size_usdc:.2f})",
            intended.size_usdc, ev.price, ev.side,
        )
    else:
        side = (ev.side or "").upper()
        slip = await rc.get_float("slippage_bps", s.slippage_bps) / 10000.0
        paused = await is_globally_paused()
        if mode == "paper" and side == "SELL":
            # Mirror the target's EXIT: close our position in this asset.
            if not await rc.get_bool("mirror_exits", s.mirror_exits):
                d = Decision("SKIPPED_DRY_RUN", "exit-mirroring off", 0.0, ev.price, ev.side)
            elif paused:
                d = Decision("SKIPPED_PAUSED", "globally paused (/panic active)", 0.0, ev.price, ev.side)
            else:
                our = await paper_position_size(chat_id, ev.asset)
                if our <= 1e-9:
                    d = Decision("SKIPPED_RISK", "no paper position to exit", 0.0, ev.price, ev.side)
                else:
                    px = min(0.999, max(0.001, ev.price * (1 - slip)))  # sell slightly worse
                    fill_id = await write_paper_fill(
                        chat_id=chat_id, ev=ev, intended_usdc=our * px, fill_price=px)
                    d = Decision("PAPER_FILLED", f"exit {our:.1f} sh @ {px:.3f} (mirrored sell)",
                                 our * px, px, ev.side, paper_fill_id=fill_id)
        else:
            intended = size_trade(ev.usdc_size, follow, min_order)
            # Per-position cap: never put more than max_position_pct of bankroll on one bet.
            cap_pct = await rc.get_float("max_position_pct", s.max_position_pct)
            size_usd = intended.size_usdc
            if cap_pct > 0 and size_usd > 0:
                size_usd = min(size_usd, cap_pct / 100.0 * s.paper_starting_bankroll_usd)
            if intended.skip_reason == "BELOW_MIN_ORDER" or size_usd < min_order:
                d = Decision("SKIPPED_BELOW_MIN",
                             f"sized to ${size_usd:.2f} < ${min_order:.2f} minimum",
                             size_usd, ev.price, ev.side)
            else:
                risk = await check_caps(chat_id=chat_id, follow=follow, intended_size_usdc=size_usd)
                if risk is not None:
                    d = Decision("SKIPPED_RISK", risk, size_usd, ev.price, ev.side)
                elif mode == "paper":
                    cash = await paper_cash(chat_id)
                    px = min(0.999, max(0.001, ev.price * (1 + slip)))  # buy slightly worse
                    if paused:
                        d = Decision("SKIPPED_PAUSED", "globally paused (/panic active)",
                                     size_usd, ev.price, ev.side)
                    elif cash < size_usd:
                        d = Decision("SKIPPED_RISK",
                                     f"insufficient paper cash (${cash:.2f} < ${size_usd:.2f})",
                                     size_usd, ev.price, ev.side)
                    else:
                        fill_id = await write_paper_fill(
                            chat_id=chat_id, ev=ev, intended_usdc=size_usd, fill_price=px)
                        d = Decision("PAPER_FILLED", f"virtual fill ${size_usd:.2f} @ {px:.3f}",
                                     size_usd, px, ev.side, paper_fill_id=fill_id)
                elif mode == "live":
                    # The owner's per-wallet opt-in is the only path to a live order.
                    if not follow.auto_execute:
                        d = Decision("SKIPPED_DRY_RUN", "auto_execute=false for this wallet",
                                     size_usd, ev.price, ev.side)
                    elif paused:
                        d = Decision("SKIPPED_PAUSED", "globally paused (/panic active)",
                                     size_usd, ev.price, ev.side)
                    else:
                        d = await _execute_live(ev, size_usd, follow)
                else:
                    d = Decision("FAILED", f"unknown trade_mode={mode}", size_usd, ev.price, ev.side)

    async with session_scope() as session:
        session.add(CopyDecision(
            chat_id=chat_id,
            target_wallet=ev.wallet,
            target_tx_hash=ev.transaction_hash,
            decision=d.decision,
            reason=d.reason,
            intended_size_usd=d.intended_size_usd or None,
            intended_price=d.intended_price or None,
            intended_side=d.intended_side or None,
            own_tx_hash=d.own_tx_hash,
            own_filled_size=d.own_filled_size,
            own_filled_price=d.own_filled_price,
        ))
    log.info(
        "decision.recorded",
        decision=d.decision,
        reason=d.reason,
        wallet=ev.wallet,
        size=d.intended_size_usd,
    )
    return d
