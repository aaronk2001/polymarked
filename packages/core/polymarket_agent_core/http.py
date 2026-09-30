from __future__ import annotations

import asyncio
import random
from collections.abc import Mapping
from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)


class TokenBucket:
    def __init__(self, rate_per_sec: float, capacity: int) -> None:
        self.rate = rate_per_sec
        self.capacity = capacity
        self.tokens = float(capacity)
        self._last: float | None = None
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            if self._last is None:
                self._last = now
            elapsed = now - self._last
            self._last = now
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            if self.tokens < 1.0:
                wait = (1.0 - self.tokens) / self.rate
                await asyncio.sleep(wait)
                self.tokens = 0.0
            else:
                self.tokens -= 1.0


class PolymarketHttpClient:
    def __init__(
        self,
        base_url: str = "https://data-api.polymarket.com",
        rate_per_sec: float = 5.0,
        max_retries: int = 4,
        timeout: float = 20.0,
    ) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout)
        self._bucket = TokenBucket(rate_per_sec=rate_per_sec, capacity=max(1, int(rate_per_sec)))
        self._max_retries = max_retries

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> PolymarketHttpClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def get_json(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        for attempt in range(self._max_retries + 1):
            await self._bucket.acquire()
            try:
                resp = await self._client.get(path, params=params)
            except httpx.TransportError as e:
                if attempt == self._max_retries:
                    raise
                await self._backoff(attempt, reason=f"transport: {e}")
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == self._max_retries:
                    resp.raise_for_status()
                retry_after = resp.headers.get("Retry-After")
                await self._backoff(attempt, reason=f"http {resp.status_code}", retry_after=retry_after)
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError("unreachable")

    async def _backoff(
        self,
        attempt: int,
        reason: str,
        retry_after: str | None = None,
    ) -> None:
        if retry_after:
            try:
                wait = float(retry_after)
            except ValueError:
                wait = 1.0
        else:
            wait = (2 ** attempt) + random.random()
        log.warning("http.retry", attempt=attempt, reason=reason, wait=wait)
        await asyncio.sleep(wait)
