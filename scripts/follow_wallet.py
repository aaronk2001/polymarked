"""Add a follow row + immediate 24h activity backfill for a wallet."""
from __future__ import annotations

import argparse
import asyncio

from polymarket_agent_core.config import load_settings
from polymarket_agent_core.db import session_scope
from polymarket_agent_core.logging import configure_logging
from polymarket_agent_core.models import Follow
from polymarket_agent_watcher import WalletWatcher
from sqlalchemy.dialects.sqlite import insert as sqlite_insert


async def _amain(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="follow_wallet")
    p.add_argument("wallet")
    p.add_argument("--nickname")
    p.add_argument("--no-backfill", action="store_true")
    args = p.parse_args(argv)

    configure_logging()
    s = load_settings()
    chat_id = s.telegram_owner_chat_id or 0

    async with session_scope() as session:
        stmt = sqlite_insert(Follow).values(
            chat_id=chat_id,
            proxy_wallet=args.wallet,
            nickname=args.nickname,
            copy_ratio=s.default_copy_ratio,
            max_per_trade_usd=s.default_max_per_trade_usd,
            daily_loss_cap_usd=s.default_daily_loss_cap_usd,
        ).on_conflict_do_update(
            index_elements=["chat_id", "proxy_wallet"],
            set_={"nickname": args.nickname, "paused": False},
        )
        await session.execute(stmt)

    print(f"Followed {args.wallet}" + (f" ({args.nickname})" if args.nickname else ""))

    if not args.no_backfill:
        import asyncio as _aio
        watcher = WalletWatcher(queue=_aio.Queue())
        try:
            count = await watcher.backfill(args.wallet)
            print(f"Backfilled {count} activity events.")
        finally:
            await watcher.aclose()
    return 0


def cli_entry() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    cli_entry()
