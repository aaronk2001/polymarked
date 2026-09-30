"""/follow, /unfollow, /mute, /unmute."""
from __future__ import annotations

from polymarket_agent_core.config import load_settings
from polymarket_agent_core.db import session_scope
from polymarket_agent_core.models import Follow
from sqlalchemy import delete
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from telegram import Update
from telegram.ext import ContextTypes

from ..auth import owner_only


@owner_only
async def follow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if not args:
        await update.effective_message.reply_text("Usage: /follow 0x... [nickname]")
        return
    wallet = args[0].lower()
    nickname = " ".join(args[1:]) or None
    s = load_settings()
    chat_id = update.effective_chat.id
    async with session_scope() as session:
        stmt = sqlite_insert(Follow).values(
            chat_id=chat_id,
            proxy_wallet=wallet,
            nickname=nickname,
            copy_ratio=s.default_copy_ratio,
            max_per_trade_usd=s.default_max_per_trade_usd,
            daily_loss_cap_usd=s.default_daily_loss_cap_usd,
        ).on_conflict_do_update(
            index_elements=["chat_id", "proxy_wallet"],
            set_={"nickname": nickname, "paused": False},
        )
        await session.execute(stmt)
    await update.effective_message.reply_text(
        f"Following {wallet}" + (f" ({nickname})" if nickname else "")
    )


@owner_only
async def unfollow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if not args:
        await update.effective_message.reply_text("Usage: /unfollow 0x...")
        return
    wallet = args[0].lower()
    chat_id = update.effective_chat.id
    async with session_scope() as session:
        await session.execute(
            delete(Follow).where(Follow.chat_id == chat_id, Follow.proxy_wallet == wallet)
        )
    await update.effective_message.reply_text(f"Unfollowed {wallet}")


@owner_only
async def mute_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_paused(update, context, paused=True)


@owner_only
async def unmute_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_paused(update, context, paused=False)


async def _set_paused(update: Update, context: ContextTypes.DEFAULT_TYPE, *, paused: bool) -> None:
    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            "Usage: /mute 0x..." if paused else "Usage: /unmute 0x..."
        )
        return
    wallet = args[0].lower()
    chat_id = update.effective_chat.id
    async with session_scope() as session:
        await session.execute(
            sa_update(Follow)
            .where(Follow.chat_id == chat_id, Follow.proxy_wallet == wallet)
            .values(paused=paused)
        )
    await update.effective_message.reply_text(("Muted " if paused else "Unmuted ") + wallet)
