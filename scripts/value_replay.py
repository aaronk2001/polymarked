"""Cost-aware replay of the favorite-longshot VALUE rule.

calibration_market.py proved the bias at a *costless* mid-life mid price. This
replays the exact rule the live engine will use — buy the token in [buy_low,
buy_high], pay a spread/slippage haircut, hold to the known 0/1 outcome — so we
can see whether the edge still clears real trading costs BEFORE running it live.

    uv run python scripts/value_replay.py --markets 1500 --max-spread 0.03

Net EV clearly positive after the haircut => safe to run the live paper engine.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from collections import defaultdict
from datetime import datetime

import httpx
import structlog
from polymarket_agent_core.logging import configure_logging

log = structlog.get_logger(__name__)
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"


def _ts(s):
    if not s:
        return None
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except Exception:
        return None


async def _resolved_markets(client, want, skip=0):
    out, offset = [], skip
    while len(out) < want and offset < skip + 12000:
        r = await client.get(f"{GAMMA}/markets", params={
            "closed": "true", "limit": 500, "offset": offset,
            "order": "endDate", "ascending": "false"})
        if r.status_code != 200:
            break
        page = r.json()
        if not page:
            break
        for m in page:
            try:
                toks = json.loads(m.get("clobTokenIds") or "[]")
                pr = json.loads(m.get("outcomePrices") or "[]")
            except Exception:
                continue
            if len(toks) == 2 and len(pr) >= 1 and pr[0] in ("0", "1"):
                ct = _ts(m.get("closedTime")) or _ts(m.get("endDate"))
                st = _ts(m.get("startDate"))
                if ct and st and ct > st:
                    out.append({"tok": toks[0], "out": float(pr[0]), "st": st, "ct": ct})
        offset += 500
    return out[:want]


async def _midlife_price(client, mk):
    try:
        h = await client.get(f"{CLOB}/prices-history",
                             params={"market": mk["tok"], "interval": "max", "fidelity": "720"})
        hist = h.json().get("history", [])
    except Exception:
        return None
    pts = [(p["t"], p["p"]) for p in hist if mk["st"] <= p["t"] < mk["ct"] and 0.0 < p["p"] < 1.0]
    if not pts:
        return None
    mid = (mk["st"] + mk["ct"]) / 2
    return min(pts, key=lambda tp: abs(tp[0] - mid))[1]


async def _amain(argv=None) -> int:
    p = argparse.ArgumentParser(prog="value_replay")
    p.add_argument("--markets", type=int, default=1500)
    p.add_argument("--skip", type=int, default=0, help="skip N most-recent (older OOS cohort)")
    p.add_argument("--buy-low", type=float, default=0.55)
    p.add_argument("--buy-high", type=float, default=0.85)
    p.add_argument("--max-spread", type=float, default=0.03,
                   help="assumed round-trip spread; half is paid on entry")
    p.add_argument("--slippage-bps", type=float, default=50.0)
    p.add_argument("--concurrency", type=int, default=8)
    args = p.parse_args(argv)
    configure_logging()

    hair = args.max_spread / 2 + args.slippage_bps / 10000.0  # entry-cost haircut on price
    sem = asyncio.Semaphore(args.concurrency)
    async with httpx.AsyncClient(timeout=25) as client:
        mkts = await _resolved_markets(client, args.markets, skip=args.skip)
        log.info("value_replay.markets", n=len(mkts))

        async def one(mk):
            async with sem:
                px = await _midlife_price(client, mk)
                if px is None:
                    return None
                out = mk["out"]  # token0 resolution (1 won / 0 lost)
                p0, p1 = px, 1.0 - px
                if args.buy_low <= p0 <= args.buy_high:
                    cost = min(0.99, p0 + hair)
                    return ("t0", cost, out / cost - 1.0)            # buy token0
                if args.buy_low <= p1 <= args.buy_high:
                    cost = min(0.99, p1 + hair)
                    return ("t1", cost, (1.0 - out) / cost - 1.0)    # buy token1 (fade longshot)
                return ("skip", 0.0, 0.0)

        picks = [x for x in await asyncio.gather(*[one(m) for m in mkts]) if x]

    traded = [x for x in picks if x[0] != "skip"]
    bins = defaultdict(lambda: {"n": 0, "win": 0, "ret": 0.0})
    tot_ret = 0.0
    wins = 0
    for _side, cost, ret in traded:
        b = round(min(0.84, max(0.55, cost)) // 0.05 * 0.05, 2)
        bins[b]["n"] += 1
        bins[b]["ret"] += ret
        bins[b]["win"] += 1 if ret > 0 else 0
        tot_ret += ret
        wins += 1 if ret > 0 else 0

    n = len(traded)
    print(f"\nVALUE replay — {len(picks)} resolved binary markets, {n} in-band bets "
          f"(band {args.buy_low:.2f}-{args.buy_high:.2f}, haircut {hair*100:.2f}c/$)\n")
    print(f"{'entry bin':>10}  {'bets':>5}  {'win%':>6}  {'avg $ret':>9}")
    for b in sorted(bins):
        a = bins[b]
        print(f"  {b:.2f}+    {a['n']:>5}  {a['win']/a['n']:>5.1%}  {a['ret']/a['n']*100:>+7.1f}%")
    if n:
        print(f"\n  BLENDED: {n} bets | win {wins/n:.1%} | net EV "
              f"{tot_ret/n*100:+.2f}% per bet after costs")
        print("  (positive after haircut => the edge clears spread/fees and is safe to run live)")
    else:
        print("\n  no in-band bets — widen the band or lower thresholds")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_amain()))
