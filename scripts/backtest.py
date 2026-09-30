"""Forward-test the copy strategy: replay a wallet's real trade history through the
SAME paper rules (copy-ratio + per-position cap, slippage, mirror-exits, resolution
settlement) and report what copying them would have returned.

    uv run python scripts/backtest.py --wallet 0x... --days 90
    uv run python scripts/backtest.py --top 10 --days 90      # top-N from data/top_scores.json
    uv run python scripts/backtest.py --follows --days 90     # your current follow list

Pure simulation — does NOT touch the live paper DB.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import structlog
from polymarket_agent_core.config import load_settings
from polymarket_agent_core.http import PolymarketHttpClient
from polymarket_agent_core.logging import configure_logging
from polymarket_agent_executor.resolutions import fetch_resolutions
from polymarket_agent_ingester.activity import fetch_all_wallet_activity

log = structlog.get_logger(__name__)


async def backtest_wallet(client, wallet, *, start_ts, end_ts, bankroll, copy_ratio,
                          max_per_trade, max_pos_pct, slip) -> dict:
    events = await fetch_all_wallet_activity(
        client, wallet=wallet, event_type="TRADE", start_ts=start_ts, end_ts=end_ts, max_rows=5000)
    events.sort(key=lambda e: int(e.get("timestamp") or 0))

    cash = bankroll
    pos: dict[str, dict] = {}
    realized = 0.0
    copied = 0
    for e in events:
        side = (e.get("side") or "").upper()
        asset = e.get("asset")
        price = float(e.get("price") or 0.0)
        their_usd = float(e.get("usdcSize") or 0.0)
        if not asset or price <= 0 or price >= 1:
            continue
        if side == "BUY":
            usd = min(their_usd * copy_ratio, max_per_trade)
            if max_pos_pct > 0:
                usd = min(usd, max_pos_pct / 100.0 * bankroll)
            if usd < 1.0 or usd > cash:
                continue
            fill = min(0.999, price * (1 + slip))
            p = pos.setdefault(asset, {"shares": 0.0, "cost": 0.0, "cond": e.get("conditionId")})
            p["shares"] += usd / fill
            p["cost"] += usd
            cash -= usd
            copied += 1
        elif side == "SELL":
            p = pos.get(asset)
            if not p or p["shares"] <= 1e-9:
                continue
            fill = max(0.001, price * (1 - slip))
            proceeds = p["shares"] * fill
            realized += proceeds - p["cost"]
            cash += proceeds
            p["shares"] = 0.0
            p["cost"] = 0.0
            copied += 1

    open_assets = [a for a, p in pos.items() if p["shares"] > 1e-9]
    res = await fetch_resolutions(open_assets) if open_assets else {}
    pos_value = unreal = 0.0
    for a in open_assets:
        p = pos[a]
        rp = res.get(a)
        mark = rp if rp is not None else (p["cost"] / p["shares"])  # cost basis if unresolved
        val = p["shares"] * mark
        pos_value += val
        unreal += val - p["cost"]
    total = cash + pos_value
    return {
        "wallet": wallet, "copied": copied,
        "final": round(total, 2), "return_pct": round((total - bankroll) / bankroll * 100, 2),
        "realized": round(realized, 2), "unrealized": round(unreal, 2),
        "open": len(open_assets),
    }


async def _amain(argv=None) -> int:
    s = load_settings()
    p = argparse.ArgumentParser(prog="backtest")
    p.add_argument("--wallet")
    p.add_argument("--top", type=int, help="backtest top-N from data/top_scores.json")
    p.add_argument("--follows", action="store_true", help="backtest current follow list")
    p.add_argument("--days", type=int, default=90, help="length of each window")
    p.add_argument("--windows", type=int, default=1, help="number of back-to-back windows (robustness)")
    p.add_argument("--bankroll", type=float, default=s.paper_starting_bankroll_usd)
    p.add_argument("--copy-ratio", type=float, default=s.default_copy_ratio)
    p.add_argument("--max-per-trade", type=float, default=s.default_max_per_trade_usd)
    args = p.parse_args(argv)
    configure_logging()

    wallets: list[str] = []
    wscore: dict[str, float] = {}
    if args.wallet:
        wallets = [args.wallet.lower()]
    elif args.top:
        rows = json.loads(Path("data/top_scores.json").read_text(encoding="utf-8"))
        wallets = [r["wallet"].lower() for r in rows[:args.top]]
        wscore = {r["wallet"].lower(): r.get("smart_score") for r in rows[:args.top]}
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
    nwin = max(1, args.windows)
    # window k covers [now-(k+1)*span, now-k*span]; W1 = most recent
    windows = [(now - (k + 1) * span, now - k * span) for k in range(nwin)]

    kw = dict(bankroll=args.bankroll, copy_ratio=args.copy_ratio,
              max_per_trade=args.max_per_trade, max_pos_pct=s.max_position_pct,
              slip=s.slippage_bps / 10000.0)
    rows_out = []
    async with PolymarketHttpClient() as client:
        for w in wallets:
            rets = []
            for (st, en) in windows:
                try:
                    r = await backtest_wallet(client, w, start_ts=st, end_ts=en, **kw)
                    rets.append(r["return_pct"])
                except Exception as e:
                    log.warning("backtest_failed", wallet=w, error=str(e))
                    rets.append(None)
            vals = [x for x in rets if x is not None]
            rows_out.append({
                "wallet": w, "rets": rets, "score": wscore.get(w),
                "mean": (sum(vals) / len(vals)) if vals else 0.0,
                "pos": sum(1 for x in vals if x > 0), "n": len(vals),
            })

    have_scores = any(r["score"] is not None for r in rows_out)
    if have_scores:  # order by Smart Score (the ranking), to read score-vs-return down the column
        rows_out.sort(key=lambda r: -(r["score"] or 0))
    else:
        rows_out.sort(key=lambda r: r["mean"], reverse=True)
    print(f"\nRobustness: copy each wallet over {nwin}×{args.days}d windows @ "
          f"{args.copy_ratio*100:.1f}% (cap {s.max_position_pct:.0f}%/pos, "
          f"{s.slippage_bps:.0f}bps slip), ${args.bankroll:,.0f}/window\n")
    hdr = "  ".join(f"W{k+1:>5}" for k in range(nwin))
    print(f"{'score':>5}   {hdr}   {'mean':>6}  {'+ve':>5}  wallet")
    for r in rows_out:
        cells = "  ".join((f"{x:>+5.1f}%" if x is not None else "   n/a") for x in r["rets"])
        sc = f"{r['score']:>5.1f}" if r["score"] is not None else "    -"
        print(f"{sc}   {cells}   {r['mean']:>+5.1f}%  {r['pos']}/{r['n']:<3}  {r['wallet']}")
    consistent = [r for r in rows_out if r["n"] >= 2 and r["pos"] == r["n"]]
    print(f"\nW1 = most recent {args.days}d, older to the right.")
    print(f"consistently positive (every window): {len(consistent)}/{len(rows_out)}")
    for r in consistent:
        print(f"  {r['wallet']}  mean {r['mean']:+.1f}%")

    if have_scores:
        scored = [(r["score"], r["mean"]) for r in rows_out if r["score"] is not None]
        rho = _spearman([a for a, _ in scored], [b for _, b in scored])
        overall = sum(b for _, b in scored) / len(scored) if scored else 0.0
        print(f"\n── Does Smart Score predict copy return? (n={len(scored)}) ──")
        print(f"Spearman rank corr(score, mean return) = "
              f"{rho:+.2f}" if rho is not None else "n/a")
        print("  ~0 = score has NO predictive power; >+0.3 = weak; >+0.6 = real signal.")
        print(f"overall mean return across all sampled wallets: {overall:+.1f}% / {args.days}d window")
    return 0


def _spearman(xs, ys):
    n = len(xs)
    if n < 3:
        return None

    def ranks(v):
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    den = (sum((rx[i] - mx) ** 2 for i in range(n)) * sum((ry[i] - my) ** 2 for i in range(n))) ** 0.5
    return num / den if den else None


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_amain()))
