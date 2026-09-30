"""Score one wallet's Smart Score from live /activity data."""
from __future__ import annotations

import argparse
import asyncio
import json

from polymarket_agent_core.http import PolymarketHttpClient
from polymarket_agent_core.logging import configure_logging
from polymarket_agent_ingester.activity import fetch_all_wallet_activity
from polymarket_agent_scoring import compute_smart_score


async def _amain(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="score_wallet")
    p.add_argument("wallet", help="0x... proxy wallet address")
    p.add_argument("--max-rows", type=int, default=5000)
    p.add_argument("--json", action="store_true")
    p.add_argument("--trades-only", action="store_true",
                   help="Fetch only TRADE events (legacy, naive PnL).")
    args = p.parse_args(argv)

    configure_logging()
    event_type = "TRADE" if args.trades_only else None
    async with PolymarketHttpClient() as c:
        trades = await fetch_all_wallet_activity(
            c, wallet=args.wallet, event_type=event_type, max_rows=args.max_rows,
        )

    report = compute_smart_score(trades)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        flags = ",".join(report.red_flags) or "-"
        print(
            f"{args.wallet}  score={report.smart_score:5.1f}  tier={report.tier:<6}  "
            f"trades={report.trade_count:<5}  pnl=${report.realized_pnl_total:>14,.0f}  "
            f"flags={flags}"
        )
    return 0


def cli_entry() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    cli_entry()
