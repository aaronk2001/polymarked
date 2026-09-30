"""Settle resolved paper positions: book realized PnL + return cash for any
open position whose Polymarket market has resolved.

    uv run python scripts/settle_resolved.py            # dry run (no writes)
    uv run python scripts/settle_resolved.py --commit   # actually settle
"""
from __future__ import annotations

import argparse
import asyncio

from polymarket_agent_core.config import load_settings
from polymarket_agent_executor import paper_snapshot, settle_resolved_positions


async def main(commit: bool) -> None:
    cid = load_settings().telegram_owner_chat_id
    before, _ = await paper_snapshot(cid)
    res = await settle_resolved_positions(cid, commit=commit)
    print(f"{'COMMIT' if commit else 'DRY RUN'} — chat_id={cid}")
    print(f"  resolved positions found: {res.settled}  (won {res.won} / lost {res.lost})")
    print(f"  cash returned (proceeds):  ${res.proceeds:,.2f}")
    print(f"  realized PnL booked:       ${res.realized_pnl:,.2f}")
    if commit:
        after, _ = await paper_snapshot(cid)
        print(f"  open positions: {before.open_positions} -> {after.open_positions}")
        print(f"  cash:           ${before.cash:,.2f} -> ${after.cash:,.2f}")
        print(f"  realized PnL:   ${before.realized_pnl:,.2f} -> ${after.realized_pnl:,.2f}")
    else:
        print("  (re-run with --commit to write these settlement fills)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true", help="write settlement fills (default: dry run)")
    asyncio.run(main(ap.parse_args().commit))
