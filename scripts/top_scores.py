"""Scrape + score the top-N wallets by Smart Score (the scoring method in use).

Candidate pool = Polymarket's monthly leaderboard (OVERALL/MONTH/PNL by default).
Each candidate is scored with the SAME compute_smart_score the app uses, then
ranked. Writes data/top_scores.csv + .json and prints a table. The sweep core
lives in polymarket_agent_ingester.sweep so the supervisor auto-sweep reuses it.

    uv run python scripts/top_scores.py                       # top 50 from 200 candidates
    uv run python scripts/top_scores.py --candidates 500 --top 50
    uv run python scripts/top_scores.py --max-rows 5000 --concurrency 6
"""
from __future__ import annotations

import argparse
import asyncio
import time

import structlog
from polymarket_agent_core.http import PolymarketHttpClient
from polymarket_agent_core.logging import configure_logging
from polymarket_agent_ingester.sweep import sweep_top_scores, write_top_scores

log = structlog.get_logger(__name__)


async def _amain(argv=None) -> int:
    p = argparse.ArgumentParser(prog="top_scores")
    p.add_argument("--candidates", type=int, default=200, help="leaderboard pool size to score")
    p.add_argument("--top", type=int, default=50, help="how many to keep")
    p.add_argument("--max-rows", type=int, default=2000, help="activity rows per wallet")
    p.add_argument("--concurrency", type=int, default=6)
    p.add_argument("--category", default="OVERALL")
    p.add_argument("--time-period", default="MONTH")
    p.add_argument("--order-by", default="PNL")
    p.add_argument("--out", default="data/top_scores")
    args = p.parse_args(argv)

    configure_logging()
    t0 = time.perf_counter()
    async with PolymarketHttpClient() as client:
        top = await sweep_top_scores(
            client, candidates=args.candidates, top=args.top, max_rows=args.max_rows,
            concurrency=args.concurrency, category=args.category,
            time_period=args.time_period, order_by=args.order_by,
        )
    if not top:
        print("No candidates returned from leaderboard.")
        return 1

    path = write_top_scores(top, args.out)
    print(f"\nTop {len(top)} wallets by Smart Score (in {time.perf_counter()-t0:.0f}s)\n")
    print(f"{'#':>3}  {'score':>5}  {'tier':<6}  {'trades':>6}  {'realized pnl':>15}  {'name':<22}  wallet")
    for i, r in enumerate(top, 1):
        nm = (r["name"] or "")[:22]
        print(f"{i:>3}  {r['smart_score']:>5.1f}  {r['tier']:<6}  {r['trades']:>6}  "
              f"${r['realized_pnl']:>13,.0f}  {nm:<22}  {r['wallet']}")
    print(f"\nWrote {path} and {path.with_suffix('.csv')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_amain()))
