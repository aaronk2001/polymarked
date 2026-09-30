"""Telegram dispatch: per-trade alerts + periodic live profit/number updates.

MarkdownV2 note: inside `code spans` only ` and \\ are special, so all numbers
(which contain . , + -) are wrapped in backticks to avoid escaping bugs.
"""
from __future__ import annotations

import asyncio
import time

import structlog
from polymarket_agent_core import heartbeat
from polymarket_agent_core import runtime_config as rc
from polymarket_agent_core.config import load_settings
from polymarket_agent_executor import paper_snapshot, record_decision
from polymarket_agent_executor.decisions import Decision
from polymarket_agent_watcher import TradeDetected
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application

from .auth import md2_escape

log = structlog.get_logger(__name__)

# Only these decisions are worth a per-trade DM; the rest are routine skips/noise.
_ALERT_DECISIONS = {"PAPER_FILLED", "EXECUTED", "FAILED"}


def _format(ev: TradeDetected, dec: Decision) -> str:
    title = md2_escape(ev.title or "(unknown market)")
    outcome = md2_escape(ev.outcome or "")
    side = md2_escape(ev.side.upper())
    decision_line = md2_escape(f"{dec.decision}: {dec.reason}")
    return (
        f"*{side}* `{ev.wallet[:14]}...`\n"
        f"{title}\n"
        f"_{outcome}_  `@ ${ev.price:.3f}`\n"
        f"size `{ev.size:,.0f}`  `${ev.usdc_size:,.2f}`\n"
        f"→ _{decision_line}_"
    )


def format_balance_summary(bal) -> str:
    """MarkdownV2 live profit/numbers card. All numbers go in code spans."""
    tpnl = bal.realized_pnl + bal.unrealized_pnl
    ret = ((bal.total - bal.starting_bankroll) / bal.starting_bankroll * 100.0) if bal.starting_bankroll else 0.0
    sign = "🟢" if tpnl >= 0 else "🔴"
    return (
        f"{sign} *PolyMarked paper*\n"
        f"Total value `${bal.total:,.2f}`  `{ret:+.2f}%`\n"
        f"Total P&L `${tpnl:+,.2f}`\n"
        f"  realized `${bal.realized_pnl:+,.2f}`  unrealized `${bal.unrealized_pnl:+,.2f}`\n"
        f"Cash `${bal.cash:,.2f}`  positions `${bal.position_value:,.2f}`\n"
        f"Open `{bal.open_positions}`  fills `{bal.fills}`"
    )


def _keyboard(ev: TradeDetected) -> InlineKeyboardMarkup:
    market_url = (
        f"https://polymarket.com/event/{ev.event_slug}" if ev.event_slug else "https://polymarket.com"
    )
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("View market", url=market_url),
        InlineKeyboardButton("Mute", callback_data=f"mute:{ev.wallet}"),
        InlineKeyboardButton("Auto-exec on", callback_data=f"autoexec:{ev.wallet}"),
    ]])


async def alert_pump(app: Application, queue: asyncio.Queue[TradeDetected], stop: asyncio.Event) -> None:
    """Drain TradeDetected events: record decision -> Telegram alert with decision embedded."""
    s = load_settings()
    chat_id = s.telegram_owner_chat_id
    if chat_id == 0:
        log.warning("alerts.no_owner_chat_id_skip")
        return
    while not stop.is_set():
        try:
            ev = await asyncio.wait_for(queue.get(), timeout=1.0)
        except TimeoutError:
            continue
        heartbeat.beat("bot.alert_pump")
        try:
            decision = await record_decision(ev)
        except Exception as e:
            log.error("alerts.decision_failed", error=str(e), wallet=ev.wallet)
            continue
        # Per-trade alerts are OFF by default and, when on, only for real outcomes.
        if not await rc.get_bool("telegram_trade_alerts_enabled", s.telegram_trade_alerts_enabled):
            continue
        if decision.decision not in _ALERT_DECISIONS:
            continue
        try:
            await app.bot.send_message(
                chat_id=chat_id,
                text=_format(ev, decision),
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=_keyboard(ev),
                disable_web_page_preview=True,
            )
        except Exception as e:
            log.error("alerts.send_failed", error=str(e), wallet=ev.wallet)


async def profit_pump(app: Application, stop: asyncio.Event) -> None:
    """Push live profit/number updates to the owner: on a fixed cadence
    (telegram_profit_update_interval_seconds) and/or when total P&L moves by
    >= profit_alert_threshold_usd. Both runtime-editable; 0 disables each."""
    s = load_settings()
    chat_id = s.telegram_owner_chat_id
    if chat_id == 0:
        return
    last_sent = 0.0
    last_pnl: float | None = None
    while not stop.is_set():
        await asyncio.sleep(15)
        if stop.is_set():
            break
        try:
            if not await rc.get_bool("telegram_alerts_enabled", s.telegram_alerts_enabled):
                continue
            interval = await rc.get_int(
                "telegram_profit_update_interval_seconds", s.telegram_profit_update_interval_seconds)
            threshold = await rc.get_float("profit_alert_threshold_usd", s.profit_alert_threshold_usd)
            if interval <= 0 and threshold <= 0:
                continue
            bal, _ = await paper_snapshot(chat_id)
            tpnl = bal.realized_pnl + bal.unrealized_pnl
            now = time.monotonic()
            due_periodic = interval > 0 and (now - last_sent) >= interval
            due_threshold = threshold > 0 and last_pnl is not None and abs(tpnl - last_pnl) >= threshold
            if due_periodic or due_threshold:
                await app.bot.send_message(
                    chat_id=chat_id,
                    text=format_balance_summary(bal),
                    parse_mode=ParseMode.MARKDOWN_V2,
                    disable_web_page_preview=True,
                )
                last_sent = now
                last_pnl = tpnl
                heartbeat.beat("bot.profit_pump")
            elif last_pnl is None:
                last_pnl = tpnl  # seed baseline silently
        except Exception as e:
            log.error("alerts.profit_pump_failed", error=str(e))
