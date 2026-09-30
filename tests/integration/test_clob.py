"""CLOB live-path integration tests.

These tests REQUIRE:
  * A funded Polygon Amoy (or mainnet, if you really mean it) proxy wallet
  * POLYMARKET_PRIVATE_KEY + POLYMARKET_FUNDER_ADDRESS in .env
  * USDC + CTF allowances already approved (run scripts/set_allowances.py)
  * CLOB_HOST pointed at the right chain
  * A token_id passed via PYTEST_CLOB_TOKEN_ID env var

They are gated by the `integration` marker so they only run when explicitly
requested:

    uv run pytest -m integration tests/integration/test_clob.py
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration

REQUIRED_ENV = ("POLYMARKET_PRIVATE_KEY", "POLYMARKET_FUNDER_ADDRESS", "PYTEST_CLOB_TOKEN_ID")


def _missing_env() -> list[str]:
    return [k for k in REQUIRED_ENV if not os.environ.get(k)]


@pytest.fixture(scope="module", autouse=True)
def _gate_env() -> None:
    missing = _missing_env()
    if missing:
        pytest.skip(f"integration env not set: {missing}")


@pytest.mark.asyncio
async def test_get_client_initializes() -> None:
    from polymarket_agent_executor.clob import get_client, reset_client_cache

    reset_client_cache()
    client = await get_client()
    assert client is not None


@pytest.mark.asyncio
async def test_creds_persisted_across_calls() -> None:
    from polymarket_agent_core.db import session_scope
    from polymarket_agent_core.models import SystemKV
    from polymarket_agent_executor.clob import CREDS_KEY, get_client, reset_client_cache
    from sqlalchemy import select

    reset_client_cache()
    await get_client()
    async with session_scope() as session:
        v = (await session.execute(select(SystemKV.value).where(SystemKV.key == CREDS_KEY))).scalar_one_or_none()
    assert v is not None and len(v) > 0


@pytest.mark.asyncio
async def test_order_book_round_trip() -> None:
    from polymarket_agent_executor.clob import get_order_book

    token_id = os.environ["PYTEST_CLOB_TOKEN_ID"]
    book = await get_order_book(token_id)
    assert book is not None
    # At least one side must have liquidity for the test to be meaningful
    assert book.bids or book.asks


@pytest.mark.asyncio
async def test_fok_reject_returns_not_ok() -> None:
    from polymarket_agent_executor.clob import place_fok

    token_id = os.environ["PYTEST_CLOB_TOKEN_ID"]
    # BUY at 1¢ won't match any ask; FOK must cancel.
    res = await place_fok(token_id=token_id, side="BUY", size=1.0, price=0.01)
    assert res.ok is False
    assert res.reason  # Must surface a reason string
