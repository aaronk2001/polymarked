"""Per-wallet activity polling loop. Emits TradeDetected events to an asyncio.Queue."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable

import structlog
from polymarket_agent_core import heartbeat
from polymarket_agent_core.config import load_settings
from polymarket_agent_core.db import session_scope
from polymarket_agent_core.http import PolymarketHttpClient
from polymarket_agent_core.models import Follow
from polymarket_agent_ingester.activity import (
    fetch_all_wallet_activity,
    fetch_wallet_activity,
)
from sqlalchemy import select

from .events import TradeDetected
from .persistence import latest_seen_ts, persist_activity

log = structlog.get_logger(__name__)


class WalletWatcher:
    def __init__(self, queue: asyncio.Queue[TradeDetected]) -> None:
        self._queue = queue
        self._settings = load_settings()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._stop_event = asyncio.Event()
        self._client = PolymarketHttpClient()

    async def aclose(self) -> None:
        self._stop_event.set()
        for t in self._tasks.values():
            t.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        await self._client.aclose()

    async def load_active_follows(self) -> list[str]:
        async with session_scope() as session:
            stmt = select(Follow.proxy_wallet).where(Follow.paused.is_(False)).distinct()
            result = await session.execute(stmt)
            return [row for (row,) in result.all()]

    async def watch(self, wallets: Iterable[str]) -> None:
        for w in wallets:
            self._spawn(w)
        await self._stop_event.wait()

    def _spawn(self, wallet: str) -> None:
        if wallet in self._tasks:
            return
        self._tasks[wallet] = asyncio.create_task(
            self._poll_loop(wallet), name=f"watcher-{wallet[:10]}"
        )

    async def _poll_loop(self, wallet: str) -> None:
        interval = self._settings.watch_poll_interval_seconds
        while not self._stop_event.is_set():
            try:
                await self._poll_once(wallet)
            except Exception as e:
                log.error("watcher.poll_error", wallet=wallet, error=str(e))
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
                return
            except TimeoutError:
                continue

    async def _poll_once(self, wallet: str) -> None:
        cursor = await latest_seen_ts(wallet)
        params_start = (cursor + 1) if cursor is not None else None
        page = await fetch_wallet_activity(
            self._client,
            wallet=wallet,
            event_type="TRADE",
            start_ts=params_start,
            limit=200,
        )
        if not page:
            heartbeat.beat(f"watcher.{wallet}")
            heartbeat.beat("watcher")
            return
        await persist_activity(wallet, page)
        for ev in sorted(page, key=lambda e: int(e.get("timestamp", 0))):
            if ev.get("type") != "TRADE":
                continue
            await self._queue.put(TradeDetected.from_raw(wallet, ev))
        heartbeat.beat(f"watcher.{wallet}")
        heartbeat.beat("watcher")
        log.info("watcher.poll_emitted", wallet=wallet, count=len(page))

    async def backfill(self, wallet: str, hours_back: int | None = None) -> int:
        """Fetch the last N hours of activity for a freshly-followed wallet and persist."""
        hours = hours_back if hours_back is not None else self._settings.watch_max_backfill_hours
        start_ts = int(time.time()) - hours * 3600
        events = await fetch_all_wallet_activity(
            self._client,
            wallet=wallet,
            event_type=None,
            start_ts=start_ts,
            max_rows=2000,
        )
        await persist_activity(wallet, events)
        return len(events)
