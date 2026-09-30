"""Calibration / favorite-longshot bias test — an edge that needs NO copying.

For thousands of resolved markets, bucket trades by price and check whether each
price actually resolves YES that often. Perfect market => actual YES rate == price
in every bucket (zero edge). Favorite-longshot bias (documented in betting markets)
=> cheap longshots resolve YES LESS than their price (overpriced, fade them) and
expensive favorites resolve YES MORE than their price (underpriced, buy them).

Any bucket where |actual - price| is large + consistent is a mechanical edge.

    uv run python scripts/calibration.py --candidates 200 --days 365

Read-only. Uses resolved markets only.
"""
from __future__ import annotations

import argparse
import asyncio
import time

import structlog
from polymarket_agent_core.http import PolymarketHttpClient
from polymarket_agent_core.logging import configure_logging
from polymarket_agent_core.schemas import LeaderboardEntry
from polymarket_agent_executor.resolutions import fetch_resolutions
from polymarket_agent_ingester.activity import fetch_all_wallet_activity
from polymarket_agent_ingester.leaderboard import fetch_leaderboard_slice

log = structlog.get_logger(__name__)


async def _pool(client, n):
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


async def _amain(argv=None) -> int:
    p = argparse.ArgumentParser(prog="calibration")
    p.add_argument("--candidates", type=int, default=200)
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--max-rows", type=int, default=2000)
    p.add_argument("--concurrency", type=int, default=8)
    args = p.parse_args(argv)
    configure_logging()
    now = int(time.time())
    start = now - args.days * 86400

    sem = asyncio.Semaphore(args.concurrency)

    async def buys(client, w):
        async with sem:
            try:
                evs = await fetch_all_wallet_activity(
                    client, wallet=w, event_type="TRADE", start_ts=start, end_ts=now, max_rows=args.max_rows)
                return [(e["asset"], float(e["price"]), float(e.get("usdcSize") or 0.0))
                        for e in evs
                        if (e.get("side") or "").upper() == "BUY"
                        and e.get("asset") and 0.0 < float(e.get("price") or 0) < 1.0]
            except Exception as ex:
                log.warning("buys_failed", wallet=w, error=str(ex))
                return []

    async with PolymarketHttpClient() as client:
        wallets = await _pool(client, args.candidates)
        log.info("calibration.pool", wallets=len(wallets))
        per = await asyncio.gather(*[buys(client, w) for w in wallets])
        trades = [t for sub in per for t in sub if t[2] > 0]
        assets = list({t[0] for t in trades})
        res = await fetch_resolutions(assets) if assets else {}

    # bucket each trade (price, outcome) by 0.05-wide price bins
    bins = [(round(0.05 * i, 2), round(0.05 * (i + 1), 2)) for i in range(20)]
    agg = {b: {"n": 0, "usd": 0.0, "win": 0, "psum": 0.0, "ret": 0.0} for b in bins}
    for asset, price, usd in trades:
        out = res.get(asset)
        if out is None:
            continue
        b = bins[min(19, int(price / 0.05))]
        a = agg[b]
        a["n"] += 1
        a["usd"] += usd
        a["psum"] += price
        a["win"] += 1 if out >= 0.5 else 0
        a["ret"] += usd * (out - price) / price  # $ edge of buying this share to resolution

    print(f"\nCalibration — {len(trades)} buys across {len([1 for b in agg if agg[b]['n']])} "
          f"price buckets ({args.candidates} wallets, {args.days}d)\n")
    print(f"{'price bin':>11}  {'trades':>7}  {'avg px':>6}  {'actual YES':>10}  {'edge':>7}  {'$ret':>7}")
    for b in bins:
        a = agg[b]
        if a["n"] < 30:
            continue
        avgp = a["psum"] / a["n"]
        actual = a["win"] / a["n"]
        edge = (actual - avgp) * 100  # cents per share the market is off
        dret = a["ret"] / a["usd"] * 100 if a["usd"] else 0
        flag = "  BUY" if dret > 3 else ("  fade" if dret < -3 else "")
        print(f"  {b[0]:.2f}-{b[1]:.2f}  {a['n']:>7}  {avgp:>6.3f}  {actual:>9.1%}  "
              f"{edge:>+6.1f}  {dret:>+6.1f}%{flag}")
    print("\nactual YES = how often that price actually resolved YES.")
    print("edge = actual − price (cents mispriced). $ret = buy-and-hold return at that price.")
    print("Consistent + buckets across many markets = a mechanical, no-copy edge.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_amain()))
