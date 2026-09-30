"""Resolved-market valuation via Polymarket's public Gamma API.

When a market resolves, its CLOB order book disappears (marks.py gets a 404), so
an open paper position in it can't be marked live. Gamma still serves the closed
market with final `outcomePrices` of "1" (won) / "0" (lost). We map each token to
its resolved price so closed positions value correctly instead of at last fill.

Resolutions are final, so positive results are cached for the process lifetime;
"not closed yet / not found" is negative-cached for a shorter window.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Iterable

import httpx
import structlog
from polymarket_agent_core.config import load_settings

log = structlog.get_logger(__name__)

# token_id -> resolved price (0.0/1.0). Final once known.
_resolved: dict[str, float] = {}
# token_id -> monotonic ts of last "not closed" lookup (negative cache).
_unresolved: dict[str, float] = {}

_BATCH = 20


async def _fetch_batch(client: httpx.AsyncClient, tokens: list[str]) -> dict[str, float]:
    params: list[tuple[str, str]] = [("clob_token_ids", t) for t in tokens]
    params.append(("closed", "true"))
    params.append(("limit", str(len(tokens) + 5)))
    out: dict[str, float] = {}
    try:
        r = await client.get("/markets", params=params)
        if r.status_code != 200:
            return out
        for m in r.json():
            if not m.get("closed"):
                continue
            try:
                ids = json.loads(m.get("clobTokenIds") or "[]")
                prices = json.loads(m.get("outcomePrices") or "[]")
            except (json.JSONDecodeError, TypeError):
                continue
            for i, tok in enumerate(ids):
                if i < len(prices):
                    with contextlib.suppress(TypeError, ValueError):
                        out[tok] = float(prices[i])
    except Exception:
        pass
    return out


async def fetch_resolutions(token_ids: Iterable[str]) -> dict[str, float]:
    """Return {token_id: resolved_price} for tokens whose market has closed."""
    s = load_settings()
    neg_ttl = s.resolution_negative_ttl_seconds
    now = time.monotonic()
    out: dict[str, float] = {}
    todo: list[str] = []
    for t in {t for t in token_ids if t}:
        if t in _resolved:
            out[t] = _resolved[t]
        elif t in _unresolved and now - _unresolved[t] < neg_ttl:
            continue  # known-open within TTL
        else:
            todo.append(t)

    if not todo:
        return out

    batches = [todo[i:i + _BATCH] for i in range(0, len(todo), _BATCH)]
    sem = asyncio.Semaphore(4)
    async with httpx.AsyncClient(base_url=s.gamma_host, timeout=10.0) as client:
        async def _go(batch: list[str]) -> dict[str, float]:
            async with sem:
                return await _fetch_batch(client, batch)

        results = await asyncio.gather(*[_go(b) for b in batches])

    found: dict[str, float] = {}
    for r in results:
        found.update(r)
    for t in todo:
        if t in found:
            _resolved[t] = found[t]
            out[t] = found[t]
        else:
            _unresolved[t] = now
    if found:
        log.debug("resolutions.found", requested=len(todo), resolved=len(found))
    return out
