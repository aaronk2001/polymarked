"""/start, /help, /list."""
from __future__ import annotations

from polymarket_agent_core.db import session_scope
from polymarket_agent_core.models import Follow
from sqlalchemy import select
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from ..auth import md2_escape, owner_only


@owner_only
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = (
        "*PolyMarked* online\\.\n"
        "Commands: /list /follow /unfollow /score /leaderboard /mute /unmute /help"
    )
    await update.effective_message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2)


@owner_only
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    body = (
        "/follow `0x...` \\[nickname\\]  add a wallet to your follow list\n"
        "/unfollow `0x...`              remove a wallet\n"
        "/list                          show all follows\n"
        "/mute `0x...` / /unmute `0x...`\n"
        "/score `0x...`                 compute Smart Score\n"
        "/leaderboard \\[CATEGORY\\]      show top 10 from latest snapshot\n"
    )
    await update.effective_message.reply_text(body, parse_mode=ParseMode.MARKDOWN_V2)


@owner_only
async def list_follows(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    async with session_scope() as session:
        stmt = select(Follow).where(Follow.chat_id == chat_id).order_by(Follow.created_ts)
        rows = list((await session.execute(stmt)).scalars())
    if not rows:
        await update.effective_message.reply_text("No follows.")
        return
    lines = ["*Follows:*"]
    for f in rows:
        marker = "🔇" if f.paused else ("⚡" if f.auto_execute else "·")
        nick = md2_escape(f.nickname or "")
        lines.append(f"{marker} `{md2_escape(f.proxy_wallet)}`  {nick}")
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2)
