"""Print full Smart Score component breakdown for one wallet."""
from __future__ import annotations

import argparse
import asyncio

from polymarket_agent_core.http import PolymarketHttpClient
from polymarket_agent_core.logging import configure_logging
from polymarket_agent_ingester.activity import fetch_all_wallet_activity
from polymarket_agent_scoring import compute_smart_score, explain


async def _amain(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="explain_score")
    p.add_argument("wallet")
    p.add_argument("--max-rows", type=int, default=5000)
    p.add_argument("--trades-only", action="store_true",
                   help="Fetch only TRADE events (legacy, naive PnL).")
    args = p.parse_args(argv)

    configure_logging()
    event_type = "TRADE" if args.trades_only else None
    async with PolymarketHttpClient() as c:
        trades = await fetch_all_wallet_activity(
            c, wallet=args.wallet, event_type=event_type, max_rows=args.max_rows,
        )
    print(explain(compute_smart_score(trades), wallet=args.wallet))
    return 0


def cli_entry() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    cli_entry()
