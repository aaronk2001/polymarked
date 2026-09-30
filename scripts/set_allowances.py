"""One-time USDC + CTF approvals for the proxy wallet. Run once before TRADE_MODE=live."""
from __future__ import annotations

import argparse
import asyncio

from polymarket_agent_core.logging import configure_logging
from polymarket_agent_executor.allowances import approve_usdc_and_ctf


async def _amain(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="set_allowances")
    p.add_argument("--rpc", default=None, help="Polygon RPC URL (default: https://polygon-rpc.com)")
    args = p.parse_args(argv)

    configure_logging()
    print("Submitting allowance txs to Polygon mainnet...")
    txs = await approve_usdc_and_ctf(rpc_url=args.rpc)
    print()
    for label, h in txs.items():
        print(f"  {label:<26}  https://polygonscan.com/tx/{h}")
    print()
    print("Wait ~60s for confirmation, then flip TRADE_MODE=live in .env.")
    return 0


def cli_entry() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    cli_entry()
