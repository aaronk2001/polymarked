"""Run watcher + bot + FastAPI dashboard in one asyncio loop."""
from __future__ import annotations

import asyncio
import contextlib
import os
import webbrowser
from pathlib import Path

import httpx
import structlog
import uvicorn
from polymarket_agent_api.main import app as api_app
from polymarket_agent_core import heartbeat
from polymarket_agent_core import runtime_config as rc
from polymarket_agent_core.config import load_settings
from polymarket_agent_core.db import get_engine, session_scope
from polymarket_agent_core.logging import configure_logging
from polymarket_agent_core.models import PaperEquity
from polymarket_agent_executor import (
    VALUE_CHAT_ID,
    paper_snapshot,
    scan_value_markets,
    settle_resolved_positions,
)
from polymarket_agent_watcher import TradeDetected, WalletWatcher

log = structlog.get_logger(__name__)


async def _acquire_single_instance() -> None:
    """Kill any prior PolyMarked instance (PID in data/polymarked.lock) before we
    bind the port / Telegram poller. Prevents the zombie-instance launch hangs."""
    lock = Path("data/polymarked.lock")
    try:
        if lock.exists():
            old = lock.read_text(encoding="utf-8").strip()
            if old.isdigit() and int(old) != os.getpid():
                pid = int(old)  # validated integer (isdigit) — safe to pass as arg
                log.warning("supervisor.killing_stale_instance", pid=pid)
                if os.name == "nt":
                    import subprocess
                    subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                                   capture_output=True, check=False)
                else:
                    with contextlib.suppress(OSError):
                        os.kill(pid, 15)
                await asyncio.sleep(1.5)
    except Exception as e:
        log.error("supervisor.single_instance_failed", error=str(e))
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(str(os.getpid()), encoding="utf-8")
    except Exception:
        pass


async def _ensure_tables() -> None:
    """Idempotently create tables added after the initial alembic baseline."""
    async with get_engine().begin() as conn:
        await conn.run_sync(PaperEquity.__table__.create, checkfirst=True)


def _books() -> list[int]:
    """Both paper books we maintain: the copy book (owner) + the value book."""
    return [load_settings().telegram_owner_chat_id, VALUE_CHAT_ID]


async def _run_equity_recorder() -> None:
    """Snapshot each book's mark-to-market paper equity on a fixed cadence so the
    dashboard can chart performance over a 24/7 run (copy book + value book)."""
    s = load_settings()
    interval = max(15, s.equity_snapshot_interval_seconds)
    while True:
        for chat_id in _books():
            try:
                bal, _ = await paper_snapshot(chat_id)
                async with session_scope() as session:
                    session.add(PaperEquity(
                        chat_id=chat_id,
                        cash=bal.cash,
                        position_value=bal.position_value,
                        realized_pnl=bal.realized_pnl,
                        unrealized_pnl=bal.unrealized_pnl,
                        total=bal.total,
                        open_positions=bal.open_positions,
                        fills=bal.fills,
                    ))
            except Exception as e:
                log.error("supervisor.equity_snapshot_failed", chat_id=chat_id, error=str(e))
        heartbeat.beat("equity_recorder")
        await asyncio.sleep(interval)


async def _announce_when_ready() -> None:
    """Poll /api/v1/health until the dashboard serves, then print a plain-text
    banner (visible regardless of LOG_FORMAT=json) and open the browser once."""
    s = load_settings()
    url = f"http://{s.api_host}:{s.api_port}"
    health = f"{url}/api/v1/health"
    deadline = asyncio.get_event_loop().time() + 45.0
    payload: dict | None = None
    async with httpx.AsyncClient(timeout=2.0) as c:
        while asyncio.get_event_loop().time() < deadline:
            try:
                r = await c.get(health)
                if r.status_code == 200:
                    payload = r.json()
                    break
            except Exception:
                pass
            await asyncio.sleep(0.5)

    if payload is None:
        print(
            "\n*** PolyMarked dashboard did not come up within 45s. "
            "See data/logs/polymarked.log for details. ***\n",
            flush=True,
        )
        return

    mode = payload.get("trade_mode", s.trade_mode)
    follows = "?"
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            fr = await c.get(f"{url}/api/v1/follows")
            if fr.status_code == 200:
                follows = str(len(fr.json()))
    except Exception:
        pass

    print(
        "\n"
        "============================================================\n"
        " PolyMarked is READY\n"
        f" Dashboard:  {url}\n"
        f" Mode:       {mode}   |   Wallets followed: {follows}\n"
        " Leave this window open. Close it to stop the app.\n"
        "============================================================\n",
        flush=True,
    )

    if os.environ.get("POLYMARKED_OPEN_BROWSER", "1") not in ("0", "false", "False", ""):
        try:
            webbrowser.open(url)
        except Exception as e:
            log.warning("supervisor.browser_open_failed", error=str(e))


async def _run_api() -> None:
    s = load_settings()
    config = uvicorn.Config(
        api_app,
        host=s.api_host,
        port=s.api_port,
        log_level=s.log_level.lower(),
        access_log=False,
    )
    server = uvicorn.Server(config)
    await server.serve()


async def _run_bot_and_watcher() -> None:
    """Same flow as polymarket_agent_bot.app.run_bot_with_watcher but degrades
    gracefully if TELEGRAM_BOT_TOKEN is unset (then runs watcher only)."""
    s = load_settings()
    queue: asyncio.Queue[TradeDetected] = asyncio.Queue()
    watcher = WalletWatcher(queue=queue)
    wallets = await watcher.load_active_follows()
    stop = asyncio.Event()

    bot_app = None
    profit_task = None
    if s.telegram_bot_token:
        from polymarket_agent_bot.alerts import alert_pump, profit_pump
        from polymarket_agent_bot.app import build_application

        bot_app = build_application()
        await bot_app.initialize()
        await bot_app.start()
        await bot_app.updater.start_polling()
        pump_task = asyncio.create_task(alert_pump(bot_app, queue, stop))
        profit_task = asyncio.create_task(profit_pump(bot_app, stop))
    else:
        log.warning("supervisor.no_bot_token_drain_only")
        async def _drain() -> None:
            from polymarket_agent_executor import record_decision
            while not stop.is_set():
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=1.0)
                except TimeoutError:
                    continue
                try:
                    await record_decision(ev)
                except Exception as e:
                    log.error("supervisor.decision_failed", error=str(e))
        pump_task = asyncio.create_task(_drain())

    watch_task = asyncio.create_task(watcher.watch(wallets)) if wallets else None
    log.info("supervisor.online", wallets=len(wallets), trade_mode=s.trade_mode, bot=bool(bot_app))

    try:
        await asyncio.Event().wait()
    finally:
        stop.set()
        if watch_task:
            watch_task.cancel()
        pump_task.cancel()
        if profit_task is not None:
            profit_task.cancel()
        await watcher.aclose()
        if bot_app is not None:
            await bot_app.updater.stop()
            await bot_app.stop()
            await bot_app.shutdown()


async def _run_settlement() -> None:
    """Auto-settle resolved positions in both books when the interval > 0 (0 = off).
    Re-reads the interval each tick so the Settings tab can toggle it live. The
    copy book honors the stop-loss; the value book is buy-and-hold (no stop —
    cutting a strong-favorite early would forfeit the edge)."""
    s = load_settings()
    owner = s.telegram_owner_chat_id
    last_run = 0.0
    while True:
        await asyncio.sleep(30)
        try:
            interval = await rc.get_int(
                "settle_resolved_interval_seconds", s.settle_resolved_interval_seconds)
            stop_price = await rc.get_float("stop_loss_price", s.stop_loss_price)
            if interval <= 0:
                continue
            now = asyncio.get_event_loop().time()
            if now - last_run < interval:
                continue
            last_run = now
            for chat_id in _books():
                stop = stop_price if chat_id == owner else 0.0
                res = await settle_resolved_positions(chat_id, commit=True, stop_price=stop)
                if res.settled:
                    log.info("supervisor.settled", chat_id=chat_id, count=res.settled, won=res.won,
                             lost=res.lost, stopped=res.stopped, realized=round(res.realized_pnl, 2))
            heartbeat.beat("settlement")
        except Exception as e:
            log.error("supervisor.settlement_failed", error=str(e))


async def _run_value_scan() -> None:
    """Scan open markets for favorite-longshot value bets on the runtime cadence
    (value_scan_interval_seconds > 0 and value_enabled). 0/off = idle. Re-reads
    both knobs each tick so the Settings tab can toggle it live."""
    s = load_settings()
    last_run = 0.0
    while True:
        await asyncio.sleep(30)
        try:
            interval = await rc.get_int(
                "value_scan_interval_seconds", s.value_scan_interval_seconds)
            enabled = await rc.get_bool("value_enabled", s.value_enabled)
            if interval <= 0 or not enabled:
                continue
            now = asyncio.get_event_loop().time()
            if now - last_run < interval:
                continue
            last_run = now
            res = await scan_value_markets(commit=True)
            if res.opened:
                log.info("supervisor.value_opened", opened=res.opened,
                         invested=round(res.invested, 2), cash_left=round(res.cash_left, 2))
            heartbeat.beat("value_scan")
        except Exception as e:
            log.error("supervisor.value_scan_failed", error=str(e))


async def _follow_top_rows(rows: list[dict], n: int) -> int:
    """Watch-only follow of the top-N swept wallets (NEVER auto-buys). New rows are
    picked up by the watcher on the next restart."""
    from polymarket_agent_core.models import Follow
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    s = load_settings()
    chat_id = s.telegram_owner_chat_id
    followed = 0
    async with session_scope() as session:
        for i, r in enumerate(rows[: max(1, n)]):
            w = (r.get("wallet") or "").lower()
            if not (w.startswith("0x") and len(w) == 42):
                continue
            nick = r.get("name") or f"#{i+1} score {r.get('smart_score')}"
            stmt = sqlite_insert(Follow).values(
                chat_id=chat_id, proxy_wallet=w, nickname=nick,
                copy_ratio=s.default_copy_ratio,
                max_per_trade_usd=s.default_max_per_trade_usd,
                daily_loss_cap_usd=s.default_daily_loss_cap_usd,
            ).on_conflict_do_update(
                index_elements=["chat_id", "proxy_wallet"],
                set_={"nickname": nick, "paused": False},
            )
            await session.execute(stmt)
            followed += 1
    return followed


async def _run_sweep() -> None:
    """Periodically re-scrape + re-score the leaderboard into data/top_scores.json
    when sweep_enabled and sweep_interval_seconds > 0 (0/off = idle). Re-reads the
    knobs each tick so Settings can toggle it live. PURE OPS: Smart Score doesn't
    predict copy return and copy-to-resolution is net-negative (scripts/copy_latency
    .py) — this only keeps the ranking fresh; auto-follow is watch-only."""
    from polymarket_agent_core.http import PolymarketHttpClient
    from polymarket_agent_ingester.sweep import sweep_top_scores, write_top_scores
    s = load_settings()
    last_run = 0.0
    while True:
        await asyncio.sleep(60)
        try:
            interval = await rc.get_int("sweep_interval_seconds", s.sweep_interval_seconds)
            enabled = await rc.get_bool("sweep_enabled", s.sweep_enabled)
            if interval <= 0 or not enabled:
                continue
            now = asyncio.get_event_loop().time()
            if now - last_run < interval:
                continue
            last_run = now
            cands = await rc.get_int("sweep_candidates", s.sweep_candidates)
            top = await rc.get_int("sweep_top_n", s.sweep_top_n)
            max_rows = await rc.get_int("sweep_max_rows", s.sweep_max_rows)
            async with PolymarketHttpClient() as client:
                rows = await sweep_top_scores(client, candidates=cands, top=top, max_rows=max_rows)
            if rows:
                write_top_scores(rows)
                extra = {}
                if await rc.get_bool("sweep_auto_follow", s.sweep_auto_follow):
                    fn = await rc.get_int("sweep_follow_n", s.sweep_follow_n)
                    extra["auto_followed"] = await _follow_top_rows(rows, fn)
                log.info("supervisor.sweep_written", count=len(rows), **extra)
            heartbeat.beat("sweep")
        except Exception as e:
            log.error("supervisor.sweep_failed", error=str(e))


async def run_supervisor() -> None:
    configure_logging()
    await _acquire_single_instance()
    try:
        await _ensure_tables()
    except Exception as e:
        log.error("supervisor.ensure_tables_failed", error=str(e))
    api_task = asyncio.create_task(_run_api(), name="api")
    bot_task = asyncio.create_task(_run_bot_and_watcher(), name="bot+watcher")
    equity_task = asyncio.create_task(_run_equity_recorder(), name="equity-recorder")
    settle_task = asyncio.create_task(_run_settlement(), name="settlement")
    value_task = asyncio.create_task(_run_value_scan(), name="value-scan")
    sweep_task = asyncio.create_task(_run_sweep(), name="sweep")
    ready_task = asyncio.create_task(_announce_when_ready(), name="ready-announce")
    log.info("supervisor.spawned",
             tasks=["api", "bot+watcher", "equity-recorder", "settlement", "value-scan", "sweep"])
    done, pending = await asyncio.wait(
        {api_task, bot_task}, return_when=asyncio.FIRST_EXCEPTION
    )
    equity_task.cancel()
    settle_task.cancel()
    value_task.cancel()
    sweep_task.cancel()
    ready_task.cancel()
    for t in pending:
        t.cancel()
    for t in done:
        exc = t.exception()
        if exc:
            log.error("supervisor.task_crashed", task=t.get_name(), error=str(exc))


def main() -> None:
    asyncio.run(run_supervisor())


if __name__ == "__main__":
    main()
