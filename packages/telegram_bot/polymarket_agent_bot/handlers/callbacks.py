"""Inline-button callback handlers (Mute, Auto-exec)."""
from __future__ import annotations

from polymarket_agent_core.db import session_scope
from polymarket_agent_core.models import Follow
from sqlalchemy import update as sa_update
from telegram import Update
from telegram.ext import ContextTypes

from ..auth import owner_only


@owner_only
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if q is None or q.data is None:
        return
    await q.answer()
    parts = q.data.split(":", 2)
    if len(parts) < 2:
        return
    action, wallet = parts[0], parts[1]
    chat_id = update.effective_chat.id
    if action == "mute":
        async with session_scope() as session:
            await session.execute(
                sa_update(Follow)
                .where(Follow.chat_id == chat_id, Follow.proxy_wallet == wallet)
                .values(paused=True)
            )
        await q.edit_message_reply_markup(reply_markup=None)
    elif action == "autoexec":
        async with session_scope() as session:
            await session.execute(
                sa_update(Follow)
                .where(Follow.chat_id == chat_id, Follow.proxy_wallet == wallet)
                .values(auto_execute=True)
            )
        await q.edit_message_reply_markup(reply_markup=None)
