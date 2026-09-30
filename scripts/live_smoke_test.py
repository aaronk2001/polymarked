"""End-to-end CLOB smoke test for the live trading path.

What it verifies, in order:
    1. Settings are sane (private key + funder set; CLOB host reachable).
    2. L2 creds derive (or load from DB) via clob.get_client().
    3. An order book fetch round-trips for a known token.
    4. A FOK order priced way out of book is REJECTED (the cancel path).
    5. (Optional, --place-fill) A tiny FOK at the touch price FILLS.

Usage:

    # Dry-run: just probe creds + order book, no fills attempted.
    uv run python scripts/live_smoke_test.py --token-id 0xdeadbeef

    # Full smoke: also place + verify a 1-cent FOK that should NOT fill.
    uv run python scripts/live_smoke_test.py --token-id 0xdeadbeef --place-reject

    # End-to-end with a real fill (CHANGES STATE — only run with funded testnet wallet):
    uv run python scripts/live_smoke_test.py --token-id 0xdeadbeef \
        --place-reject --place-fill --fill-size 1 --side BUY

Prereqs:
    POLYMARKET_PRIVATE_KEY, POLYMARKET_FUNDER_ADDRESS set
    CLOB_HOST (defaults to mainnet — use Amoy CLOB host for testnet)
    For --place-fill: USDC + CTF allowances already approved (see scripts/set_allowances.py)
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict

import structlog
from polymarket_agent_core.config import load_settings
from polymarket_agent_core.logging import configure_logging

log = structlog.get_logger("smoke")


async def _check_settings() -> bool:
    s = load_settings()
    issues: list[str] = []
    if not s.polymarket_private_key:
        issues.append("POLYMARKET_PRIVATE_KEY is empty")
    if not s.polymarket_funder_address:
        issues.append("POLYMARKET_FUNDER_ADDRESS is empty")
    if not s.clob_host:
        issues.append("CLOB_HOST is empty")
    print(f"[1/5] settings: chain_id={s.polymarket_chain_id} clob_host={s.clob_host}")
    for i in issues:
        print(f"      FAIL: {i}")
    return not issues


async def _check_creds() -> bool:
    from polymarket_agent_executor.clob import get_client
    print("[2/5] deriving / loading L2 creds...")
    try:
        client = await get_client()
        print(f"      ok: client={type(client).__name__}")
        return True
    except Exception as e:
        print(f"      FAIL: {e}")
        return False


async def _check_book(token_id: str) -> tuple[bool, float | None, float | None]:
    from polymarket_agent_executor.clob import get_order_book
    print(f"[3/5] fetching order book for token {token_id[:18]}...")
    try:
        book = await get_order_book(token_id)
        best_bid = float(book.bids[0].price) if book.bids else None
        best_ask = float(book.asks[0].price) if book.asks else None
        print(f"      ok: best_bid={best_bid} best_ask={best_ask} "
              f"({len(book.bids)} bids / {len(book.asks)} asks)")
        return True, best_bid, best_ask
    except Exception as e:
        print(f"      FAIL: {e}")
        return False, None, None


async def _check_reject(token_id: str, side: str, best_bid: float | None, best_ask: float | None) -> bool:
    """Place a FOK at a price guaranteed to not match. Should be rejected, not filled."""
    from polymarket_agent_executor.clob import place_fok
    # For BUY, an absurdly low price won't match any ask. For SELL, absurdly high won't match any bid.
    bad_price = 0.01 if side.upper() == "BUY" else 0.99
    print(f"[4/5] placing reject-guaranteed FOK: side={side} price={bad_price} size=1")
    try:
        res = await place_fok(token_id=token_id, side=side, size=1.0, price=bad_price)
        if res.ok:
            print(f"      FAIL: unexpected fill: {asdict(res)}")
            return False
        print(f"      ok: rejected as expected: reason={res.reason}")
        return True
    except Exception as e:
        print(f"      FAIL: {e}")
        return False


async def _check_fill(token_id: str, side: str, size: float, best_bid: float | None, best_ask: float | None) -> bool:
    """Place a FOK priced at the touch — should fill if there's liquidity."""
    from polymarket_agent_executor.clob import place_fok
    if side.upper() == "BUY":
        if best_ask is None:
            print("[5/5] FAIL: no ask side; cannot fill a BUY")
            return False
        price = best_ask
    else:
        if best_bid is None:
            print("[5/5] FAIL: no bid side; cannot fill a SELL")
            return False
        price = best_bid
    print(f"[5/5] placing fill-guaranteed FOK: side={side} price={price} size={size}")
    try:
        res = await place_fok(token_id=token_id, side=side, size=size, price=price)
        if not res.ok:
            print(f"      FAIL: did not fill: reason={res.reason}")
            return False
        print(f"      ok: filled: order_id={res.order_id} tx={res.tx_hashes}")
        return True
    except Exception as e:
        print(f"      FAIL: {e}")
        return False


async def _amain(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="live_smoke_test", description=__doc__)
    p.add_argument("--token-id", required=True, help="CTF token id (the long ERC1155 token id)")
    p.add_argument("--side", default="BUY", choices=["BUY", "SELL"])
    p.add_argument("--place-reject", action="store_true", help="Send a reject-guaranteed FOK")
    p.add_argument("--place-fill", action="store_true", help="Send a fill-guaranteed FOK at the touch")
    p.add_argument("--fill-size", type=float, default=1.0, help="Size for the fill test")
    args = p.parse_args(argv)

    configure_logging()

    ok = await _check_settings()
    if not ok:
        return 1
    ok = await _check_creds()
    if not ok:
        return 2
    ok, best_bid, best_ask = await _check_book(args.token_id)
    if not ok:
        return 3

    if args.place_reject and not await _check_reject(
            args.token_id, args.side, best_bid, best_ask):
        return 4

    if args.place_fill and not await _check_fill(
            args.token_id, args.side, args.fill_size, best_bid, best_ask):
        return 5

    print("\nsmoke test: ALL CHECKS PASSED")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()
