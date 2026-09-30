"""Reusable Smart-Score sweep: leaderboard pool -> score each -> ranked list.

Shared by scripts/top_scores.py (CLI) and the supervisor auto-sweep task so the
two can't drift. Writes data/top_scores.{json,csv} — the file the dashboard's
Top-by-Smart-Score panel and POST /follow_top read.

NOTE: the sweep only keeps the *ranking* fresh. Smart Score does not predict
forward copy return (scripts/backtest.py: +0.09 Spearman out-of-sample), so this
is a convenience, not an edge — auto-following off it stays watch-only.
"""
from __future__ import annotations

import asyncio
import csv
import json
from pathlib import Path

import structlog
from polymarket_agent_core.http import PolymarketHttpClient
from polymarket_agent_core.schemas import LeaderboardEntry
from polymarket_agent_scoring import compute_smart_score

from polymarket_agent_ingester.activity import fetch_all_wallet_activity
from polymarket_agent_ingester.leaderboard import fetch_leaderboard_slice

log = structlog.get_logger(__name__)


async def _candidates(client, *, category, time_period, order_by, n) -> list[tuple[str, str | None]]:
    raw = await fetch_leaderboard_slice(
        client, category=category, time_period=time_period, order_by=order_by, top_n=n
    )
    out: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for r in raw:
        try:
            e = LeaderboardEntry.model_validate(r)
        except Exception:
            continue
        w = (e.proxy_wallet or "").lower()
        if w and w not in seen:
            seen.add(w)
            out.append((w, e.user_name))
    return out


async def _score_one(client, wallet, name, max_rows, sem, progress) -> dict | None:
    async with sem:
        try:
            events = await fetch_all_wallet_activity(
                client, wallet=wallet, event_type=None, max_rows=max_rows
            )
            rep = compute_smart_score(events)
            progress["done"] += 1
            if progress["done"] % 10 == 0:
                log.info("sweep.scored", done=progress["done"], total=progress["total"])
            return {
                "wallet": wallet,
                "name": name,
                "smart_score": round(rep.smart_score, 2),
                "tier": rep.tier,
                "trades": rep.trade_count,
                "realized_pnl": round(rep.realized_pnl_total, 2),
                "red_flags": rep.red_flags,
            }
        except Exception as e:
            log.warning("sweep.score_failed", wallet=wallet, error=str(e))
            progress["done"] += 1
            return None


async def sweep_top_scores(
    client: PolymarketHttpClient,
    *,
    candidates: int = 200,
    top: int = 50,
    max_rows: int = 2000,
    concurrency: int = 6,
    category: str = "OVERALL",
    time_period: str = "MONTH",
    order_by: str = "PNL",
) -> list[dict]:
    """Score a leaderboard pool and return the top-`top` rows by Smart Score (desc)."""
    cands = await _candidates(
        client, category=category, time_period=time_period, order_by=order_by, n=candidates
    )
    if not cands:
        return []
    sem = asyncio.Semaphore(concurrency)
    progress = {"done": 0, "total": len(cands)}
    results = await asyncio.gather(
        *[_score_one(client, w, n, max_rows, sem, progress) for w, n in cands]
    )
    scored = [r for r in results if r]
    scored.sort(key=lambda r: r["smart_score"], reverse=True)
    return scored[:top]


def write_top_scores(rows: list[dict], out: str = "data/top_scores") -> Path:
    """Persist the ranking to {out}.json + {out}.csv (json is the API/dashboard source)."""
    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.with_suffix(".json").open("w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)
    with p.with_suffix(".csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rank", "wallet", "name", "smart_score", "tier", "trades", "realized_pnl", "red_flags"])
        for i, r in enumerate(rows, 1):
            w.writerow([i, r["wallet"], r["name"] or "", r["smart_score"], r["tier"],
                        r["trades"], r["realized_pnl"], "|".join(r["red_flags"])])
    return p.with_suffix(".json")
