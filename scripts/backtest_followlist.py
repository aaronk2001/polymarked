"""Replay historical activity through the paper executor against your follow list."""
from __future__ import annotations

import argparse
import asyncio

from polymarket_agent_core.config import load_settings
from polymarket_agent_core.db import session_scope
from polymarket_agent_core.logging import configure_logging
from polymarket_agent_core.models import ActivityEvent, Follow
from polymarket_agent_executor import paper_balance, record_decision
from polymarket_agent_watcher import TradeDetected
from sqlalchemy import select


async def _amain(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="backtest_followlist")
    p.add_argument("--limit", type=int, default=2000, help="Max events to replay.")
    args = p.parse_args(argv)

    configure_logging()
    s = load_settings()
    chat_id = s.telegram_owner_chat_id

    async with session_scope() as session:
        follows = list((await session.execute(
            select(Follow.proxy_wallet).where(Follow.chat_id == chat_id, Follow.paused.is_(False))
        )).scalars())
    if not follows:
        print("No follows. Use scripts/follow_wallet.py first.")
        return 1

    async with session_scope() as session:
        rows = list((await session.execute(
            select(ActivityEvent)
            .where(
                ActivityEvent.proxy_wallet.in_(follows),
                ActivityEvent.event_type == "TRADE",
            )
            .order_by(ActivityEvent.timestamp)
            .limit(args.limit)
        )).scalars())
    print(f"Replaying {len(rows)} historical TRADE events across {len(follows)} follow(s)...")

    counts: dict[str, int] = {}
    for r in rows:
        ev = TradeDetected(
            wallet=r.proxy_wallet,
            transaction_hash=r.transaction_hash,
            timestamp=r.timestamp,
            asset=r.asset,
            side=r.side,
            size=r.size or 0.0,
            usdc_size=r.usdc_size or 0.0,
            price=r.price or 0.0,
            title=r.title,
            slug=r.slug,
            event_slug=r.event_slug,
            outcome=r.outcome,
            outcome_index=r.outcome_index,
            raw=r.raw or {},
        )
        d = await record_decision(ev)
        counts[d.decision] = counts.get(d.decision, 0) + 1

    bal = await paper_balance(chat_id)
    print()
    print("Decision counts:")
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<20} {v}")
    print()
    print(f"Paper balance: {bal.fills} fills, ${bal.realized_pnl:,.2f} realized, "
          f"{bal.open_positions} open positions")
    return 0


def cli_entry() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    cli_entry()
