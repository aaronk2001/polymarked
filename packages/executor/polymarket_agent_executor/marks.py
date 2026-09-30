"""Live mark-to-market prices from Polymarket's public CLOB midpoint endpoint.

No auth required (unlike clob.py's order-placement client), so paper mode works
without a funded wallet. Results are TTL-cached per token to bound request rate.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable

import httpx
import structlog
from polymarket_agent_core.config import load_settings

log = structlog.get_logger(__name__)

# token_id -> (price_or_None, monotonic_fetched_at). None means "no live book"
# (resolved/illiquid); cached separately so dead tokens aren't re-probed every tick.
_cache: dict[str, tuple[float | None, float]] = {}


async def _fetch_one(client: httpx.AsyncClient, token_id: str) -> float | None:
    try:
        r = await client.get("/midpoint", params={"token_id": token_id})
        if r.status_code != 200:
            return None
        mid = r.json().get("mid")
        return float(mid) if mid is not None else None
    except Exception:
        return None


async def fetch_marks(token_ids: Iterable[str]) -> dict[str, float]:
    """Return {token_id: midpoint_price} for the given tokens.

    Cache-first; only stale/missing tokens hit the network. Failures are simply
    omitted from the result so callers fall back to last fill price.
    """
    s = load_settings()
    pos_ttl = s.mark_cache_ttl_seconds
    neg_ttl = s.mark_negative_ttl_seconds
    now = time.monotonic()
    out: dict[str, float] = {}
    stale: list[str] = []
    for t in {t for t in token_ids if t}:
        cached = _cache.get(t)
        if cached is not None:
            price, ts = cached
            ttl = pos_ttl if price is not None else neg_ttl
            if now - ts < ttl:
                if price is not None:
                    out[t] = price
                continue  # fresh hit or known-dead within TTL
        stale.append(t)

    if not stale:
        return out

    sem = asyncio.Semaphore(8)
    async with httpx.AsyncClient(base_url=s.clob_host, timeout=6.0) as client:
        async def _go(tok: str) -> tuple[str, float | None]:
            async with sem:
                return tok, await _fetch_one(client, tok)

        results = await asyncio.gather(*[_go(t) for t in stale])

    got = 0
    for tok, price in results:
        ok = price is not None and price > 0
        _cache[tok] = (price if ok else None, now)  # cache misses too
        if ok:
            out[tok] = price
            got += 1
    if got < len(stale):
        log.debug("marks.partial", requested=len(stale), got=got)
    return out
