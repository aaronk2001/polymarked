"""py-clob-client wrapper. Lazy-init, L2 cred persistence, async via to_thread."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import structlog
from polymarket_agent_core.config import load_settings
from polymarket_agent_core.db import session_scope
from polymarket_agent_core.models import SystemKV
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

log = structlog.get_logger(__name__)

# Default; can be overridden per-call via settings.clob_host
DEFAULT_CLOB_HOST = "https://clob.polymarket.com"
CREDS_KEY = "clob_l2_creds"


@dataclass(frozen=True)
class FOKResult:
    ok: bool
    order_id: str | None
    tx_hashes: list[str]
    filled_size: float
    filled_price: float
    reason: str | None = None


_client = None
_lock = asyncio.Lock()


async def _load_persisted_creds() -> dict | None:
    async with session_scope() as session:
        row = await session.execute(select(SystemKV.value).where(SystemKV.key == CREDS_KEY))
        v = row.scalar_one_or_none()
    if not v:
        return None
    try:
        return json.loads(v)
    except Exception:
        return None


async def _persist_creds(creds: dict) -> None:
    async with session_scope() as session:
        stmt = sqlite_insert(SystemKV).values(key=CREDS_KEY, value=json.dumps(creds))
        stmt = stmt.on_conflict_do_update(index_elements=["key"], set_={"value": stmt.excluded.value})
        await session.execute(stmt)


def _build_l1_client():
    """Build a sync ClobClient with only the L1 private key set. Used to derive L2 creds."""
    from py_clob_client.client import ClobClient

    s = load_settings()
    if not s.polymarket_private_key:
        raise RuntimeError("POLYMARKET_PRIVATE_KEY is empty - cannot init CLOB client.")
    return ClobClient(
        host=s.clob_host or DEFAULT_CLOB_HOST,
        key=s.polymarket_private_key,
        chain_id=s.polymarket_chain_id,
        signature_type=s.polymarket_signature_type,
        funder=s.polymarket_funder_address or None,
    )


def _attach_creds(client, creds: dict):
    from py_clob_client.clob_types import ApiCreds

    client.set_api_creds(ApiCreds(
        api_key=creds["api_key"],
        api_secret=creds["api_secret"],
        api_passphrase=creds["api_passphrase"],
    ))
    return client


async def get_client():
    """Lazy global ClobClient. Derives L2 creds on first run and persists them."""
    global _client
    async with _lock:
        if _client is not None:
            return _client
        s = load_settings()
        # Try env first
        if s.polymarket_api_key and s.polymarket_api_secret and s.polymarket_api_passphrase:
            creds = {
                "api_key": s.polymarket_api_key,
                "api_secret": s.polymarket_api_secret,
                "api_passphrase": s.polymarket_api_passphrase,
            }
        else:
            # Try DB-persisted
            creds = await _load_persisted_creds()
            if not creds:
                # Derive (signs an L1 message). Sync call.
                log.info("clob.deriving_l2_creds")
                tmp = await asyncio.to_thread(_build_l1_client)
                api_creds = await asyncio.to_thread(tmp.create_or_derive_api_creds)
                creds = {
                    "api_key": api_creds.api_key,
                    "api_secret": api_creds.api_secret,
                    "api_passphrase": api_creds.api_passphrase,
                }
                await _persist_creds(creds)
                log.info("clob.l2_creds_persisted")
        client = await asyncio.to_thread(_build_l1_client)
        _attach_creds(client, creds)
        _client = client
        return _client


async def get_order_book(token_id: str):
    client = await get_client()
    return await asyncio.to_thread(client.get_order_book, token_id)


async def best_for_side(token_id: str, side: str) -> float | None:
    """Return best bid (for SELL) or best ask (for BUY)."""
    book = await get_order_book(token_id)
    side_u = side.upper()
    levels = book.bids if side_u == "SELL" else book.asks
    if not levels:
        return None
    # OrderBookSummary returns lists; py-clob-client orders bids descending, asks ascending.
    return float(levels[0].price)


async def place_fok(*, token_id: str, side: str, size: float, price: float) -> FOKResult:
    """Place a FOK order. Returns FOKResult."""
    from py_clob_client.clob_types import OrderArgs, OrderType
    from py_clob_client.order_builder.constants import BUY, SELL

    client = await get_client()
    side_const = BUY if side.upper() == "BUY" else SELL
    args = OrderArgs(price=price, size=size, side=side_const, token_id=token_id)
    try:
        resp = await asyncio.to_thread(client.create_and_post_order, args, OrderType.FOK)
    except Exception as e:
        log.error("clob.place_fok_exception", error=str(e))
        return FOKResult(False, None, [], 0.0, 0.0, reason=f"exception: {e}")

    if not isinstance(resp, dict):
        return FOKResult(False, None, [], 0.0, 0.0, reason=f"bad response shape: {type(resp).__name__}")

    err = resp.get("errorMsg") or ""
    if err:
        return FOKResult(False, None, [], 0.0, 0.0, reason=err)
    status = resp.get("status") or ""
    order_id = resp.get("orderID")
    tx = resp.get("transactionsHashes") or []
    # FOK either fully fills or cancels - no partials
    filled_size = float(resp.get("makingAmount") or size if status == "matched" else 0.0)
    filled_price = price
    return FOKResult(
        ok=(status == "matched"),
        order_id=order_id,
        tx_hashes=tx,
        filled_size=filled_size,
        filled_price=filled_price,
        reason=None if status == "matched" else f"status={status}",
    )


async def cancel_all() -> dict:
    client = await get_client()
    return await asyncio.to_thread(client.cancel_all)


def reset_client_cache() -> None:
    global _client
    _client = None
