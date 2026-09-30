"""Lightweight live feed for the dashboard ticker: trending Polymarket markets
(what's hot to bet on, with current odds) + sports headlines (ESPN RSS, no key).
TTL-cached so we don't hammer the sources."""
from __future__ import annotations

import json
import re
import time

import httpx
import structlog

from .config import load_settings

log = structlog.get_logger(__name__)

_ESPN_RSS = "https://www.espn.com/espn/rss/news"
_cache: dict = {"ts": 0.0, "data": []}
_TTL = 300.0


async def _trending_markets(client: httpx.AsyncClient) -> list[dict]:
    out: list[dict] = []
    try:
        r = await client.get(
            load_settings().gamma_host + "/markets",
            params={"closed": "false", "active": "true",
                    "order": "volume24hr", "ascending": "false", "limit": 10},
        )
        if r.status_code != 200:
            return out
        for m in r.json():
            try:
                prices = json.loads(m.get("outcomePrices") or "[]")
                outs = json.loads(m.get("outcomes") or "[]")
            except (json.JSONDecodeError, TypeError):
                prices, outs = [], []
            lead = ""
            if prices and outs and len(prices) == len(outs):
                i = max(range(len(prices)), key=lambda k: _f(prices[k]))
                lead = f"{outs[i]} {_f(prices[i]) * 100:.0f}%"
            out.append({
                "type": "market",
                "title": m.get("question"),
                "detail": lead,
                "slug": m.get("slug"),
            })
    except Exception as e:
        log.debug("news.markets_failed", error=str(e))
    return out


def _rss_titles(text: str, limit: int = 6) -> list[dict]:
    """Regex-extract <item> titles/links from an RSS feed. Avoids the stdlib XML
    parser entirely (no XXE / entity-expansion surface)."""
    out: list[dict] = []
    for it in re.findall(r"<item\b.*?</item>", text, re.S | re.I)[: limit * 3]:
        m = re.search(r"<title>(.*?)</title>", it, re.S | re.I)
        if not m:
            continue
        t = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", m.group(1), flags=re.S).strip()
        lk = re.search(r"<link>(.*?)</link>", it, re.S | re.I)
        if t:
            out.append({"type": "sports", "title": t, "link": lk.group(1).strip() if lk else None})
        if len(out) >= limit:
            break
    return out


async def _sports_headlines(client: httpx.AsyncClient) -> list[dict]:
    try:
        r = await client.get(_ESPN_RSS)
        return _rss_titles(r.text) if r.status_code == 200 else []
    except Exception as e:
        log.debug("news.rss_failed", error=str(e))
        return []


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


async def fetch_news() -> list[dict]:
    now = time.monotonic()
    if _cache["data"] and now - _cache["ts"] < _TTL:
        return _cache["data"]
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as c:
        markets = await _trending_markets(c)
        sports = await _sports_headlines(c)
    data = markets + sports
    if data:
        _cache.update(ts=now, data=data)
    return data
