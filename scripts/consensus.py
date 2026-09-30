"""Smart-money CONSENSUS test — the best untried idea.

Single-wallet copying is noise (validated). But maybe AGREEMENT carries signal:
do markets where MANY top wallets bought the same outcome beat markets only one
wallet bought? We sweep the consensus threshold K (>=K distinct top wallets on the
same token) and measure buy-and-hold-to-resolution return at each K. If return
rises with K, consensus is a real edge and we copy only high-consensus trades.

    uv run python scripts/consensus.py --top 50 --days 180

Pure read-only analysis. Uses resolved markets only (real outcomes).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import defaultdict
from pathlib import Path

import structlog
from polymarket_agent_core.http import PolymarketHttpClient
from polymarket_agent_core.logging import configure_logging
from polymarket_agent_core.schemas import LeaderboardEntry
from polymarket_agent_executor.resolutions import fetch_resolutions
from polymarket_agent_ingester.activity import fetch_all_wallet_activity
from polymarket_agent_ingester.leaderboard import fetch_leaderboard_slice

log = structlog.get_logger(__name__)


async def _leaderboard_pool(client, n):
    raw = await fetch_leaderboard_slice(client, category="OVERALL", time_period="ALL", order_by="PNL", top_n=n)
    out, seen = [], set()
    for r in raw:
        try:
            w = (LeaderboardEntry.model_validate(r).proxy_wallet or "").lower()
        except Exception:
            continue
        if w and w not in seen:
            seen.add(w)
            out.append(w)
    return out


async def _wallet_buys(client, wallet, start, end, max_rows):
    evs = await fetch_all_wallet_activity(
        client, wallet=wallet, event_type="TRADE", start_ts=start, end_ts=end, max_rows=max_rows)
    return [e for e in evs
            if (e.get("side") or "").upper() == "BUY"
            and e.get("asset") and 0.0 < float(e.get("price") or 0) < 1.0]


async def _amain(argv=None) -> int:
    p = argparse.ArgumentParser(prog="consensus")
    p.add_argument("--top", type=int, help="wallets from data/top_scores.json")
    p.add_argument("--candidates", type=int, help="fresh leaderboard pool of N wallets")
    p.add_argument("--days", type=int, default=180)
    p.add_argument("--max-rows", type=int, default=2500)
    p.add_argument("--concurrency", type=int, default=6)
    args = p.parse_args(argv)
    configure_logging()
    now = int(time.time())
    start = now - args.days * 86400

    # per asset: distinct wallets, total usd, sum(usd*price) for vol-weighted entry
    holders: dict[str, set] = defaultdict(set)
    usd: dict[str, float] = defaultdict(float)
    pxw: dict[str, float] = defaultdict(float)

    sem = asyncio.Semaphore(args.concurrency)

    async def go(client, w):
        async with sem:
            try:
                return w, await _wallet_buys(client, w, start, now, args.max_rows)
            except Exception as e:
                log.warning("buys_failed", wallet=w, error=str(e))
                return w, []

    async with PolymarketHttpClient() as client:
        if args.candidates:
            wallets = await _leaderboard_pool(client, args.candidates)
        else:
            rows = json.loads(Path("data/top_scores.json").read_text(encoding="utf-8"))[:(args.top or 50)]
            wallets = [r["wallet"].lower() for r in rows]
        log.info("consensus.pool", wallets=len(wallets), days=args.days)
        per = await asyncio.gather(*[go(client, w) for w in wallets])
        for w, buys in per:
            for e in buys:
                a = e["asset"]
                u = float(e.get("usdcSize") or 0.0)
                if u <= 0:
                    continue
                holders[a].add(w)
                usd[a] += u
                pxw[a] += u * float(e["price"])
        assets = list(holders.keys())
        res = await fetch_resolutions(assets) if assets else {}

    # keep resolved markets with a real outcome
    rows_a = []
    for a in assets:
        if a not in res or usd[a] <= 0:
            continue
        entry = pxw[a] / usd[a]
        if not (0.0 < entry < 1.0):
            continue
        out = res[a]
        rows_a.append({"k": len(holders[a]), "entry": entry, "out": out,
                       "ret": (out - entry) / entry, "usd": usd[a]})

    if not rows_a:
        print("No resolved consensus markets found — widen --days or --top.")
        return 1

    maxk = max(r["k"] for r in rows_a)
    print(f"\nSmart-money consensus — top {args.top} wallets, {args.days}d, "
          f"{len(rows_a)} resolved markets\n")
    print(f"{'>=K':>4}  {'markets':>7}  {'$-wtd ret':>10}  {'equal ret':>10}  {'hit%':>6}  {'avg entry':>9}")
    for K in range(1, maxk + 1):
        sub = [r for r in rows_a if r["k"] >= K]
        if len(sub) < 5:
            break
        tot_usd = sum(r["usd"] for r in sub)
        wret = sum(r["ret"] * r["usd"] for r in sub) / tot_usd * 100
        eret = sum(r["ret"] for r in sub) / len(sub) * 100
        hit = sum(1 for r in sub if r["out"] > r["entry"]) / len(sub) * 100
        ent = sum(r["entry"] for r in sub) / len(sub)
        print(f"{K:>4}  {len(sub):>7}  {wret:>+9.1f}%  {eret:>+9.1f}%  {hit:>5.1f}  {ent:>9.3f}")
    print("\n$-wtd ret = buy each consensus market $-for-$ at avg entry, hold to resolution.")
    print("If return climbs as K rises, agreement = edge → copy only high-consensus trades.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_amain()))
