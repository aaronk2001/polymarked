"""Best bid/ask from Polymarket's public CLOB order book.

Mirrors marks.py: the /book endpoint needs no auth, so paper mode can read the
spread (and the real ask we'd pay) without a funded wallet. TTL-cached per token.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable

import httpx
import structlog
from polymarket_agent_core.config import load_settings

log = structlog.get_logger(__name__)

# token_id -> ((best_bid, best_ask) | None, fetched_at). None = no/empty book.
_cache: dict[str, tuple[tuple[float, float] | None, float]] = {}


def _best(book: dict) -> tuple[float, float] | None:
    """Top-of-book: highest bid price, lowest ask price. Returns None if a side
    is missing (can't price a one-sided book)."""
    try:
        bids = [float(b["price"]) for b in book.get("bids", []) if float(b.get("size", 0)) > 0]
        asks = [float(a["price"]) for a in book.get("asks", []) if float(a.get("size", 0)) > 0]
    except (TypeError, ValueError, KeyError):
        return None
    if not bids or not asks:
        return None
    return max(bids), min(asks)


async def _fetch_one(client: httpx.AsyncClient, token_id: str) -> tuple[float, float] | None:
    try:
        r = await client.get("/book", params={"token_id": token_id})
        if r.status_code != 200:
            return None
        return _best(r.json())
    except Exception:
        return None


async def fetch_spreads(token_ids: Iterable[str]) -> dict[str, tuple[float, float]]:
    """Return {token_id: (best_bid, best_ask)} for tokens with a two-sided book.
    Cache-first; failures/one-sided books are simply omitted so the caller can
    treat "no readable book" as a skip."""
    s = load_settings()
    pos_ttl = s.mark_cache_ttl_seconds
    neg_ttl = s.mark_negative_ttl_seconds
    now = time.monotonic()
    out: dict[str, tuple[float, float]] = {}
    stale: list[str] = []
    for t in {t for t in token_ids if t}:
        cached = _cache.get(t)
        if cached is not None:
            quote, ts = cached
            ttl = pos_ttl if quote is not None else neg_ttl
            if now - ts < ttl:
                if quote is not None:
                    out[t] = quote
                continue
        stale.append(t)

    if not stale:
        return out

    sem = asyncio.Semaphore(8)
    async with httpx.AsyncClient(base_url=s.clob_host, timeout=6.0) as client:
        async def _go(tok: str) -> tuple[str, tuple[float, float] | None]:
            async with sem:
                return tok, await _fetch_one(client, tok)

        results = await asyncio.gather(*[_go(t) for t in stale])

    for tok, quote in results:
        _cache[tok] = (quote, now)
        if quote is not None:
            out[tok] = quote
    return out
