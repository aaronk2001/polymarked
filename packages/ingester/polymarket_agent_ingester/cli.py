from __future__ import annotations

import argparse
import asyncio

from polymarket_agent_core.logging import configure_logging

from .leaderboard import CATEGORIES, ORDER_BYS, TIME_PERIODS, snapshot_leaderboard


async def _amain() -> int:
    p = argparse.ArgumentParser(prog="polymarked-pull-leaderboard")
    p.add_argument("--top", type=int, default=500, help="Rows to retrieve (capped at 1050).")
    p.add_argument("--category", default="OVERALL", choices=CATEGORIES)
    p.add_argument("--time-period", default="ALL", choices=TIME_PERIODS, dest="time_period")
    p.add_argument("--order-by", default="PNL", choices=ORDER_BYS, dest="order_by")
    args = p.parse_args()

    configure_logging()
    n = await snapshot_leaderboard(
        category=args.category,
        time_period=args.time_period,
        order_by=args.order_by,
        top_n=args.top,
    )
    print(f"Wrote {n} leaderboard rows.")
    return 0


def cli_entry() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    cli_entry()
