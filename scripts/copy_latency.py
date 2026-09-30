"""DECISIVE copy-edge test: is there a LATENCY (informed-flow) edge in copying?

Selecting traders by past performance is a proven dead end here (backtest.py /
clv_score.py: Smart Score +0.09 OOS, CLV -0.19 OOS). The ONLY untested copy
mechanism with theory behind it is *speed*: a sharp wallet's NEW buy is followed
by favorable price drift, and you only capture it if you enter FAST. This replays
each wallet's BUY trades and measures the buy-and-hold-to-resolution return as a
function of how long AFTER their trade you entered (delay buckets), cost-aware,
across two non-overlapping cohorts (out-of-sample).

    uv run python scripts/copy_latency.py --top 10 --days 120 --haircut 0.02
    uv run python scripts/copy_latency.py --follows --days 120
    uv run python scripts/copy_latency.py --wallet 0x... --days 180

Read the table:
  return DECAYS with delay (instant >> slow) AND instant clears costs => REAL edge: copy fast.
  return FLAT across delays                                           => no drift: copying captures nothing new.
  return negative at every delay after costs                         => copying loses (confirms prior no-edge result).
A claim is only real if BOTH cohorts show the same shape.

Leak guards (see NOTES printed at the end): survivorship (top-PnL pool inflates the
LEVEL, not the within-trade delay-decay), thin-market fillability (mid-price proxy,
not the live ask). Pure simulation — does NOT touch the paper DB.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx
import structlog
from polymarket_agent_core.http import PolymarketHttpClient
from polymarket_agent_core.logging import configure_logging
from polymarket_agent_executor.resolutions import fetch_resolutions
from polymarket_agent_ingester.activity import fetch_all_wallet_activity

log = structlog.get_logger(__name__)
CLOB = "https://clob.polymarket.com"

# Seconds after their trade that YOU enter. delay 0 = perfect mirror.
DELAYS = [0, 60, 300, 900, 3600, 14400, 86400]
LABELS = ["0s", "1m", "5m", "15m", "1h", "4h", "24h"]
BAND_LOW, BAND_HIGH = 0.80, 0.92  # favorite band, to also test band-filtered copy


async def _price_series(http, token, start_ts, end_ts):
    """1-min CLOB price history over [start_ts, end_ts]; sorted (t, p), 0<p<1."""
    try:
        r = await http.get(f"{CLOB}/prices-history", params={
            "market": token, "startTs": int(start_ts), "endTs": int(end_ts), "fidelity": 1})
        hist = r.json().get("history", [])
    except Exception:
        return []
    out = []
    for p in hist:
        try:
            t, px = int(p["t"]), float(p["p"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0.0 < px < 1.0:
            out.append((t, px))
    out.sort()
    return out


def _price_at(series, t):
    """Last known price at or before t (what you'd realistically fill near)."""
    lo, hi, ans = 0, len(series) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if series[mid][0] <= t:
            ans = series[mid][1]
            lo = mid + 1
        else:
            hi = mid - 1
    return ans


async def _collect_buys(client, wallet, start_ts, end_ts, cap):
    events = await fetch_all_wallet_activity(
        client, wallet=wallet, event_type="TRADE", start_ts=start_ts, end_ts=end_ts, max_rows=5000)
    buys = []
    for e in events:
        if (e.get("side") or "").upper() != "BUY":
            continue
        asset = e.get("asset")
        price = float(e.get("price") or 0.0)
        t0 = int(e.get("timestamp") or 0)
        if not asset or t0 <= 0 or price <= 0 or price >= 1:
            continue
        buys.append({"asset": asset, "p0": price, "t0": t0})
    buys.sort(key=lambda b: b["t0"], reverse=True)
    return buys[:cap]


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


async def _run_cohort(client, http, wallets, st, en, haircut, cap, concurrency):
    buys = []
    for w in wallets:
        try:
            buys += await _collect_buys(client, w, st, en, cap)
        except Exception as e:
            log.warning("collect_failed", wallet=w, error=str(e))
    assets = list({b["asset"] for b in buys})
    res = await fetch_resolutions(assets) if assets else {}
    buys = [b for b in buys if res.get(b["asset"]) is not None]

    sem = asyncio.Semaphore(concurrency)

    async def one(b):
        async with sem:
            series = await _price_series(http, b["asset"], b["t0"] - 600, b["t0"] + max(DELAYS) + 3600)
            if not series:
                return None
            outcome = res[b["asset"]]
            net, gross = {}, {}
            for d in DELAYS:
                px = _price_at(series, b["t0"] + d)
                if px is None:
                    continue
                fill = min(0.999, px + haircut)
                net[d] = (outcome - fill) / fill
                gross[d] = (outcome - px) / px
            return {"p0": b["p0"], "outcome": outcome, "net": net, "gross": gross}

    return [r for r in await asyncio.gather(*[one(b) for b in buys]) if r]


def _delay_stats(rows, key="net"):
    stats = {}
    for d in DELAYS:
        vals = [r[key][d] for r in rows if d in r[key]]
        wins = [1 for r in rows if d in r[key] and r["outcome"] >= 0.5]
        stats[d] = {"n": len(vals), "mean": _mean(vals), "win": (len(wins) / len(vals)) if vals else None}
    return stats


def _verdict(net_stats):
    d0, dl = net_stats[DELAYS[0]]["mean"], net_stats[DELAYS[-1]]["mean"]
    means = [s["mean"] for s in net_stats.values() if s["mean"] is not None]
    if not means:
        return "no data"
    if d0 is not None and dl is not None and d0 > 0 and (d0 - dl) > 0.01:
        return "LATENCY EDGE present (positive instant, decays with delay)"
    if max(means) <= 0:
        return "NO EDGE (net-negative at every delay after costs)"
    return "FLAT / inconclusive (no clear instant-vs-late decay)"


def _print_cohort(name, rows, haircut):
    net = _delay_stats(rows, "net")
    gross = _delay_stats(rows, "gross")
    print(f"\n── cohort: {name}  ({len(rows)} resolved buys, {haircut*100:.1f}c haircut) ──")
    print(f"{'delay':>6}  {'n':>5}  {'gross':>7}  {'net':>7}  {'win%':>6}")
    for d, lab in zip(DELAYS, LABELS, strict=False):
        s, g = net[d], gross[d]
        if s["n"] < 20:
            print(f"{lab:>6}  {s['n']:>5}  {'n/a':>7}  {'n/a':>7}  {'n/a':>6}  (n<20)")
            continue
        print(f"{lab:>6}  {s['n']:>5}  {g['mean']*100:>+6.1f}%  {s['mean']*100:>+6.1f}%  {s['win']*100:>5.0f}%")
    # band-filtered copy (only mirror buys landing in the favorite band), instant fill
    inb = [r["net"][0] for r in rows if 0 in r["net"] and BAND_LOW <= r["p0"] <= BAND_HIGH]
    out = [r["net"][0] for r in rows if 0 in r["net"] and not (BAND_LOW <= r["p0"] <= BAND_HIGH)]
    mi, mo = _mean(inb), _mean(out)
    print(f"  band-filter @0s  in[{BAND_LOW:.2f}-{BAND_HIGH:.2f}]: "
          f"{mi*100:+.1f}% (n={len(inb)})" if mi is not None else
          f"  band-filter @0s  in-band: n/a (n={len(inb)})", end="")
    print(f"   out: {mo*100:+.1f}% (n={len(out)})" if mo is not None else f"   out: n/a (n={len(out)})")
    print(f"  verdict: {_verdict(net)}")
    return net


async def _amain(argv=None) -> int:
    p = argparse.ArgumentParser(prog="copy_latency")
    p.add_argument("--wallet")
    p.add_argument("--top", type=int, help="latency-test top-N from data/top_scores.json")
    p.add_argument("--follows", action="store_true", help="latency-test current follow list")
    p.add_argument("--days", type=int, default=120, help="length of each cohort window")
    p.add_argument("--haircut", type=float, default=0.02, help="cost haircut added to the fill price (0.02 = 2c)")
    p.add_argument("--max-per-wallet", type=int, default=50, help="most-recent N buys per wallet per cohort")
    p.add_argument("--concurrency", type=int, default=8)
    args = p.parse_args(argv)
    configure_logging()

    wallets: list[str] = []
    if args.wallet:
        wallets = [args.wallet.lower()]
    elif args.top:
        rows = json.loads(Path("data/top_scores.json").read_text(encoding="utf-8"))
        wallets = [r["wallet"].lower() for r in rows[:args.top]]
    elif args.follows:
        from polymarket_agent_core.db import session_scope
        from polymarket_agent_core.models import Follow
        from sqlalchemy import select
        async with session_scope() as sx:
            wallets = [w.lower() for w in (await sx.execute(select(Follow.proxy_wallet))).scalars()]
    if not wallets:
        print("Specify --wallet, --top N, or --follows")
        return 1

    now = int(time.time())
    span = args.days * 86400
    cohorts = [("recent", now - span, now), ("older (OOS)", now - 2 * span, now - span)]

    t0 = time.perf_counter()
    print(f"\nLatency copy-test: {len(wallets)} wallets, 2×{args.days}d cohorts, "
          f"≤{args.max_per_wallet} buys/wallet, {args.haircut*100:.1f}c haircut")
    cohort_nets = []
    async with PolymarketHttpClient() as client, httpx.AsyncClient(timeout=25) as http:
        for name, st, en in cohorts:
            rows = await _run_cohort(client, http, wallets, st, en, args.haircut,
                                     args.max_per_wallet, args.concurrency)
            if not rows:
                print(f"\n── cohort: {name} — no resolved buys with price history ──")
                cohort_nets.append(None)
                continue
            cohort_nets.append(_print_cohort(name, rows, args.haircut))

    print(f"\ndone in {time.perf_counter()-t0:.0f}s")
    print("\nNOTES / leak guards:")
    print("  • Pool = top-PnL/followed wallets (survivorship) → inflates the LEVEL, not the")
    print("    within-trade instant-vs-late DECAY. Re-run with a random/broad wallet set to de-bias the level.")
    print("  • Fill = market mid + haircut, NOT the live ask. A positive net is necessary, not sufficient;")
    print("    thin longshot books may not fill there. delay=0 ≈ perfect mirror (last tick ≤ trade time).")
    print("  • Real claim requires BOTH cohorts to show the same shape. One cohort = noise.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_amain()))
