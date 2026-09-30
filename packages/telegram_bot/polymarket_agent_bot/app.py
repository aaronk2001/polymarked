"""Bot entrypoint: build Application, register handlers, orchestrate alert pump + watcher."""
from __future__ import annotations

import asyncio

import structlog
from polymarket_agent_core.config import load_settings
from polymarket_agent_core.logging import configure_logging
from polymarket_agent_watcher import TradeDetected, WalletWatcher
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
)

from .alerts import alert_pump, profit_pump
from .handlers.admin import (
    balance_cmd,
    mode_cmd,
    panic_cmd,
    pnl_cmd,
    positions_cmd,
    unpanic_cmd,
)
from .handlers.basic import help_cmd, list_follows, start
from .handlers.callbacks import callback_handler
from .handlers.follow import follow_cmd, mute_cmd, unfollow_cmd, unmute_cmd
from .handlers.score import leaderboard_cmd, score_cmd

log = structlog.get_logger(__name__)


def build_application() -> Application:
    s = load_settings()
    if not s.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is empty - cannot start bot.")
    app = ApplicationBuilder().token(s.telegram_bot_token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("list", list_follows))
    app.add_handler(CommandHandler("follow", follow_cmd))
    app.add_handler(CommandHandler("unfollow", unfollow_cmd))
    app.add_handler(CommandHandler("mute", mute_cmd))
    app.add_handler(CommandHandler("unmute", unmute_cmd))
    app.add_handler(CommandHandler("score", score_cmd))
    app.add_handler(CommandHandler("leaderboard", leaderboard_cmd))
    app.add_handler(CommandHandler("panic", panic_cmd))
    app.add_handler(CommandHandler("unpanic", unpanic_cmd))
    app.add_handler(CommandHandler("mode", mode_cmd))
    app.add_handler(CommandHandler("balance", balance_cmd))
    app.add_handler(CommandHandler("pnl", pnl_cmd))
    app.add_handler(CommandHandler("positions", positions_cmd))
    app.add_handler(CallbackQueryHandler(callback_handler))
    return app


async def run_bot_with_watcher() -> None:
    configure_logging()
    app = build_application()
    queue: asyncio.Queue[TradeDetected] = asyncio.Queue()
    watcher = WalletWatcher(queue=queue)
    wallets = await watcher.load_active_follows()
    stop = asyncio.Event()

    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    pump_task = asyncio.create_task(alert_pump(app, queue, stop))
    profit_task = asyncio.create_task(profit_pump(app, stop))
    watch_task = asyncio.create_task(watcher.watch(wallets)) if wallets else None

    log.info("bot.online", followed_wallets=len(wallets), trade_mode=load_settings().trade_mode)
    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("bot.shutdown")
    finally:
        stop.set()
        if watch_task:
            watch_task.cancel()
        pump_task.cancel()
        profit_task.cancel()
        await watcher.aclose()
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


def main() -> None:
    asyncio.run(run_bot_with_watcher())


if __name__ == "__main__":
    main()
