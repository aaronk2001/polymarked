"""Unit tests for CLOB wrapper that don't touch the network.

We mock py_clob_client.client.ClobClient and verify our wrapping logic:
  * FOKResult shape is correct for matched, rejected, and exception paths
  * place_fok parses Polymarket response shapes correctly
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _reset_client_cache() -> None:
    from polymarket_agent_executor.clob import reset_client_cache
    reset_client_cache()
    yield
    reset_client_cache()


@pytest.mark.asyncio
async def test_place_fok_matched_status_returns_ok() -> None:
    from polymarket_agent_executor import clob as clob_mod

    fake_client = type("FakeClient", (), {})()

    def fake_create_and_post(args, ot):
        return {
            "status": "matched",
            "orderID": "order-123",
            "transactionsHashes": ["0xabc"],
            "makingAmount": 5.0,
        }

    fake_client.create_and_post_order = fake_create_and_post

    async def fake_get_client():
        return fake_client

    with patch.object(clob_mod, "get_client", fake_get_client):
        res = await clob_mod.place_fok(token_id="t", side="BUY", size=5.0, price=0.5)

    assert res.ok is True
    assert res.order_id == "order-123"
    assert res.tx_hashes == ["0xabc"]
    assert res.filled_size == 5.0
    assert res.filled_price == 0.5


@pytest.mark.asyncio
async def test_place_fok_unmatched_status_returns_not_ok() -> None:
    from polymarket_agent_executor import clob as clob_mod

    fake_client = type("FakeClient", (), {})()
    fake_client.create_and_post_order = lambda args, ot: {"status": "unmatched", "orderID": None}

    async def fake_get_client():
        return fake_client

    with patch.object(clob_mod, "get_client", fake_get_client):
        res = await clob_mod.place_fok(token_id="t", side="BUY", size=5.0, price=0.01)

    assert res.ok is False
    assert "unmatched" in (res.reason or "")
    assert res.filled_size == 0.0


@pytest.mark.asyncio
async def test_place_fok_error_msg_returns_not_ok() -> None:
    from polymarket_agent_executor import clob as clob_mod

    fake_client = type("FakeClient", (), {})()
    fake_client.create_and_post_order = lambda args, ot: {"errorMsg": "insufficient funds"}

    async def fake_get_client():
        return fake_client

    with patch.object(clob_mod, "get_client", fake_get_client):
        res = await clob_mod.place_fok(token_id="t", side="BUY", size=5.0, price=0.5)

    assert res.ok is False
    assert "insufficient funds" in (res.reason or "")


@pytest.mark.asyncio
async def test_place_fok_exception_returns_not_ok() -> None:
    from polymarket_agent_executor import clob as clob_mod

    fake_client = type("FakeClient", (), {})()

    def boom(args, ot):
        raise RuntimeError("network fell over")

    fake_client.create_and_post_order = boom

    async def fake_get_client():
        return fake_client

    with patch.object(clob_mod, "get_client", fake_get_client):
        res = await clob_mod.place_fok(token_id="t", side="BUY", size=5.0, price=0.5)

    assert res.ok is False
    assert "network fell over" in (res.reason or "")


@pytest.mark.asyncio
async def test_place_fok_bad_response_shape_returns_not_ok() -> None:
    from polymarket_agent_executor import clob as clob_mod

    fake_client = type("FakeClient", (), {})()
    fake_client.create_and_post_order = lambda args, ot: "not a dict"

    async def fake_get_client():
        return fake_client

    with patch.object(clob_mod, "get_client", fake_get_client):
        res = await clob_mod.place_fok(token_id="t", side="BUY", size=5.0, price=0.5)

    assert res.ok is False
    assert "bad response shape" in (res.reason or "")
