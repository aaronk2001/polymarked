"""CLV (Closing-Line Value) score — the research-backed skill metric.

Instead of ranking wallets by P&L/Sharpe (variance-laden, lagging), rank by how
much they BEAT THE MARKET: for each BUY, compare their entry price to where the
market ended up (resolution 1/0 if settled, else the current price). A trader who
consistently buys below where the market closes is sharp — CLV is the single best
predictor of long-term betting profitability.

    uv run python scripts/clv_score.py --top 50 --days 180        # rank top_scores.json wallets by CLV
    uv run python scripts/clv_score.py --candidates 200 --days 180  # rank a fresh leaderboard pool
    uv run python scripts/clv_score.py --wallet 0x... --days 365

Writes data/clv_scores.{csv,json}. Pure read-only analysis.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import time
from pathlib import Path

import structlog
from polymarket_agent_core.http import PolymarketHttpClient
from polymarket_agent_core.logging import configure_logging
from polymarket_agent_core.schemas import LeaderboardEntry
from polymarket_agent_executor.marks import fetch_marks
from polymarket_agent_executor.resolutions import fetch_resolutions
from polymarket_agent_ingester.activity import fetch_all_wallet_activity
from polymarket_agent_ingester.leaderboard import fetch_leaderboard_slice

log = structlog.get_logger(__name__)


async def clv_for_wallet(client, wallet, *, start_ts, end_ts, max_rows) -> dict | None:
    events = await fetch_all_wallet_activity(
        client, wallet=wallet, event_type="TRADE", start_ts=start_ts, end_ts=end_ts, max_rows=max_rows)
    buys = [e for e in events
            if (e.get("side") or "").upper() == "BUY"
            and e.get("asset") and 0.0 < float(e.get("price") or 0) < 1.0]
    if not buys:
        return None
    assets = list({e["asset"] for e in buys})
    resolved = await fetch_resolutions(assets)
    unresolved = [a for a in assets if a not in resolved]
    marks = await fetch_marks(unresolved) if unresolved else {}

    total_usd = edge_usd = clv_cents = 0.0
    n = npos = 0
    for e in buys:
        asset = e["asset"]
        entry = float(e["price"])
        usd = float(e.get("usdcSize") or 0.0)
        final = resolved.get(asset)
        if final is None:
            final = marks.get(asset)
        if final is None or usd <= 0:
            continue
        # $ you'd make buying `usd` at entry and holding to `final`
        edge_usd += usd * (final - entry) / entry
        clv_cents += (final - entry) * usd  # size-weighted price edge
        total_usd += usd
        n += 1
        npos += 1 if final > entry else 0
    if n == 0 or total_usd <= 0:
        return None
    return {
        "wallet": wallet,
        "clv_return_pct": round(edge_usd / total_usd * 100, 2),  # mirror-$-and-hold return
        "avg_clv_cents": round(clv_cents / total_usd * 100, 2),   # avg edge in cents/share
        "hit_rate": round(npos / n * 100, 1),
        "trades": n,
        "volume": round(total_usd, 0),
    }


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
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2 + 1
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    den = (sum((rx[i] - mx) ** 2 for i in range(n)) * sum((ry[i] - my) ** 2 for i in range(n))) ** 0.5
    return num / den if den else None


async def _candidates(client, n):
    raw = await fetch_leaderboard_slice(client, category="OVERALL", time_period="ALL", order_by="PNL", top_n=n)
    out, seen = [], set()
    for r in raw:
        try:
            e = LeaderboardEntry.model_validate(r)
        except Exception:
            continue
        w = (e.proxy_wallet or "").lower()
        if w and w not in seen:
            seen.add(w)
            out.append((w, None))
    return out


async def _amain(argv=None) -> int:
    p = argparse.ArgumentParser(prog="clv_score")
    p.add_argument("--wallet")
    p.add_argument("--top", type=int, help="rank wallets from data/top_scores.json (carries smart_score)")
    p.add_argument("--candidates", type=int, help="rank a fresh leaderboard pool")
    p.add_argument("--days", type=int, default=180)
    p.add_argument("--max-rows", type=int, default=3000)
    p.add_argument("--concurrency", type=int, default=6)
    p.add_argument("--out", default="data/clv_scores")
    args = p.parse_args(argv)
    configure_logging()

    smart: dict[str, float] = {}
    wallets: list[str] = []
    async with PolymarketHttpClient() as client:
        if args.wallet:
            wallets = [args.wallet.lower()]
        elif args.top:
            rows = json.loads(Path("data/top_scores.json").read_text(encoding="utf-8"))[:args.top]
            wallets = [r["wallet"].lower() for r in rows]
            smart = {r["wallet"].lower(): r.get("smart_score") for r in rows}
        elif args.candidates:
            wallets = [w for w, _ in await _candidates(client, args.candidates)]
        if not wallets:
            print("Specify --wallet, --top N, or --candidates N")
            return 1

        sem = asyncio.Semaphore(args.concurrency)
        now = int(time.time())
        start_ts = now - args.days * 86400

        async def go(w):
            async with sem:
                try:
                    return await clv_for_wallet(client, w, start_ts=start_ts, end_ts=now, max_rows=args.max_rows)
                except Exception as e:
                    log.warning("clv_failed", wallet=w, error=str(e))
                    return None

        results = [r for r in await asyncio.gather(*[go(w) for w in wallets]) if r]

    for r in results:
        r["smart_score"] = smart.get(r["wallet"])
    results.sort(key=lambda r: r["clv_return_pct"], reverse=True)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    with out.with_suffix(".csv").open("w", encoding="utf-8", newline="") as f:
        wtr = csv.writer(f)
        wtr.writerow(["rank", "wallet", "clv_return_pct", "avg_clv_cents", "hit_rate", "trades", "smart_score"])
        for i, r in enumerate(results, 1):
            wtr.writerow([i, r["wallet"], r["clv_return_pct"], r["avg_clv_cents"],
                          r["hit_rate"], r["trades"], r.get("smart_score") or ""])

    print(f"\nCLV score — {len(results)} wallets over {args.days}d "
          f"(buy each entry $-for-$, hold to resolution/now)\n")
    print(f"{'CLV%':>7}  {'edge¢':>6}  {'hit%':>5}  {'trades':>6}  {'score':>5}  wallet")
    for r in results:
        sc = f"{r['smart_score']:>5.1f}" if r.get("smart_score") is not None else "    -"
        print(f"{r['clv_return_pct']:>+6.1f}%  {r['avg_clv_cents']:>+5.1f}  {r['hit_rate']:>5.1f}  "
              f"{r['trades']:>6}  {sc}  {r['wallet']}")
    if any(r.get("smart_score") is not None for r in results):
        pairs = [(r["smart_score"], r["clv_return_pct"]) for r in results if r.get("smart_score") is not None]
        rho = _spearman([a for a, _ in pairs], [b for _, b in pairs])
        print(f"\nSpearman(smart_score, CLV) = {rho:+.2f}" if rho is not None else "")
        print("  (if ~0, Smart Score and the CLV skill metric disagree — rank by CLV.)")
    print(f"\nWrote {out.with_suffix('.csv')} and {out.with_suffix('.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_amain()))
