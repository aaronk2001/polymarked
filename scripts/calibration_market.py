"""UNBIASED calibration / favorite-longshot test.

The wallet-trade version was survivorship-biased (only winners' buys). This pulls
RESOLVED markets directly from gamma and prices each by the MARKET's own mid-life
price (CLOB price history) — not by any trader — vs the actual outcome. That's an
unbiased read on whether Polymarket prices are systematically wrong.

    uv run python scripts/calibration_market.py --markets 1500

Perfect market => actual YES rate == price in every bucket (no edge).
Favorite-longshot => longshots overpriced (fade), favorites underpriced (buy).
"""
from __future__ import annotations

import argparse
import asyncio
import json

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
        from datetime import datetime
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except Exception:
        return None


async def _resolved_markets(client, want, skip=0):
    """Paginate recent resolved markets with a clean binary 0/1 outcome.
    `skip` starts pagination deeper = an older, non-overlapping cohort (out-of-sample)."""
    out, offset = [], skip
    while len(out) < want and offset < skip + 12000:
        r = await client.get(f"{GAMMA}/markets", params={
            "closed": "true", "limit": 500, "offset": offset, "order": "endDate", "ascending": "false"})
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
            if len(toks) >= 1 and len(pr) >= 1 and pr[0] in ("0", "1"):
                ct = _ts(m.get("closedTime")) or _ts(m.get("endDate"))
                st = _ts(m.get("startDate"))
                if ct and st and ct > st:
                    out.append({"tok": toks[0], "out": float(pr[0]), "st": st, "ct": ct})
        offset += 500
    return out[:want]


async def _midlife_price(client, mk):
    """Market's own price at mid-life (unbiased snapshot before resolution)."""
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
    p = argparse.ArgumentParser(prog="calibration_market")
    p.add_argument("--markets", type=int, default=1500)
    p.add_argument("--skip", type=int, default=0, help="skip N most-recent markets (older OOS cohort)")
    p.add_argument("--concurrency", type=int, default=8)
    args = p.parse_args(argv)
    configure_logging()

    sem = asyncio.Semaphore(args.concurrency)
    async with httpx.AsyncClient(timeout=25) as client:
        mkts = await _resolved_markets(client, args.markets, skip=args.skip)
        log.info("calibration.markets", n=len(mkts))

        async def one(mk):
            async with sem:
                px = await _midlife_price(client, mk)
                return (px, mk["out"]) if px is not None else None

        pairs = [x for x in await asyncio.gather(*[one(m) for m in mkts]) if x]

    bins = [(round(0.05 * i, 2), round(0.05 * (i + 1), 2)) for i in range(20)]
    agg = {b: {"n": 0, "win": 0, "psum": 0.0, "ret": 0.0} for b in bins}
    for px, out in pairs:
        b = bins[min(19, int(px / 0.05))]
        a = agg[b]
        a["n"] += 1
        a["psum"] += px
        a["win"] += 1 if out >= 0.5 else 0
        a["ret"] += (out - px) / px  # equal-weighted buy-and-hold return at mid-life price

    print(f"\nUNBIASED calibration — {len(pairs)} resolved markets, mid-life market price vs outcome\n")
    print(f"{'price bin':>11}  {'mkts':>5}  {'avg px':>6}  {'actual YES':>10}  {'edge¢':>6}  {'$ret':>7}")
    for b in bins:
        a = agg[b]
        if a["n"] < 20:
            continue
        avgp = a["psum"] / a["n"]
        actual = a["win"] / a["n"]
        edge = (actual - avgp) * 100
        dret = a["ret"] / a["n"] * 100
        flag = "  BUY" if dret > 5 else ("  FADE" if dret < -5 else "")
        print(f"  {b[0]:.2f}-{b[1]:.2f}  {a['n']:>5}  {avgp:>6.3f}  {actual:>9.1%}  {edge:>+5.1f}  {dret:>+6.1f}%{flag}")
    print("\nactual YES = how often that mid-life price actually resolved YES (unbiased).")
    print("edge = actual − price; |edge| > ~3¢ consistently across many markets = real mispricing.")
    print("(Polymarket fee+spread is small but nonzero — an edge must clear it to be tradeable.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_amain()))
