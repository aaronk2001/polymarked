"""Owner-gating decorator. Every handler must wrap with @owner_only."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any

import structlog
from polymarket_agent_core.config import load_settings
from telegram import Update
from telegram.ext import ContextTypes

log = structlog.get_logger(__name__)

Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[Any]]


def owner_only(handler: Handler) -> Handler:
    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Any:
        owner = load_settings().telegram_owner_chat_id
        chat = update.effective_chat
        if owner == 0 or chat is None or chat.id != owner:
            log.warning(
                "bot.unauthorized",
                chat_id=getattr(chat, "id", None),
                expected=owner,
            )
            return None
        return await handler(update, context)

    return wrapper


def md2_escape(text: str | None) -> str:
    """Escape text for Telegram MarkdownV2."""
    if text is None:
        return ""
    specials = r"_*[]()~`>#+-=|{}.!\\"
    out = []
    for ch in text:
        out.append("\\" + ch if ch in specials else ch)
    return "".join(out)
