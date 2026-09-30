"""/panic, /unpanic, /mode, /balance, /pnl, /positions."""
from __future__ import annotations

from polymarket_agent_core.config import load_settings
from polymarket_agent_core.db import session_scope
from polymarket_agent_core.models import Follow
from polymarket_agent_executor import (
    get_effective_mode,
    is_globally_paused,
    paper_snapshot,
    set_globally_paused,
)
from sqlalchemy import update as sa_update
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from ..alerts import format_balance_summary
from ..auth import md2_escape, owner_only


@owner_only
async def panic_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    async with session_scope() as session:
        await session.execute(
            sa_update(Follow).where(Follow.chat_id == chat_id).values(auto_execute=False)
        )
    await set_globally_paused(True)
    await update.effective_message.reply_text(
        "PANIC: live auto_execute disabled on every follow; paper trading paused. "
        "Use /unpanic to resume paper."
    )


@owner_only
async def unpanic_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await set_globally_paused(False)
    await update.effective_message.reply_text(
        "Paper trading resumed. Live auto_execute remains OFF; toggle per-wallet to re-enable."
    )


@owner_only
async def mode_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    mode = await get_effective_mode()
    paused = await is_globally_paused()
    paused_marker = " (paused)" if paused else ""
    body = (
        f"*Mode:* `{md2_escape(mode)}`{md2_escape(paused_marker)}\n"
        f"_Set TRADE\\_MODE in \\.env, restart\\._"
    )
    await update.effective_message.reply_text(body, parse_mode=ParseMode.MARKDOWN_V2)


@owner_only
async def balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    bal, _ = await paper_snapshot(load_settings().telegram_owner_chat_id)
    await update.effective_message.reply_text(
        format_balance_summary(bal), parse_mode=ParseMode.MARKDOWN_V2)


# /pnl is an alias for the same live summary card.
pnl_cmd = balance_cmd


@owner_only
async def positions_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _, positions = await paper_snapshot(load_settings().telegram_owner_chat_id)
    if not positions:
        await update.effective_message.reply_text("No open positions.")
        return
    top = sorted(positions, key=lambda p: p.value, reverse=True)[:10]
    lines = ["*Top open positions*"]
    src = {"live": "●", "resolved": "✓", "cost": "·"}
    for p in top:
        title = md2_escape((p.title or p.asset[:12])[:38])
        lines.append(
            f"{src.get(p.price_source, '·')} {title}\n"
            f"  `${p.value:,.2f}` uP&L `${p.unrealized_pnl:+,.2f}` `{p.unrealized_pct:+.1f}%`"
        )
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2)
