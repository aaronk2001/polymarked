"""Global runtime state stored in system_kv: panic flag + trade-mode override."""
from __future__ import annotations

from polymarket_agent_core.config import load_settings
from polymarket_agent_core.db import session_scope
from polymarket_agent_core.models import SystemKV
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

PAUSED_KEY = "paper_paused"
MODE_KEY = "trade_mode_override"
VALID_MODES = ("off", "paper", "live")
# A runtime override can only step DOWN from the .env mode. Live is reachable only via
# TRADE_MODE=live in .env plus a restart, so no API call or bot command can turn it on.
OVERRIDE_MODES = ("off", "paper")


async def _get(key: str) -> str | None:
    async with session_scope() as session:
        row = await session.execute(select(SystemKV.value).where(SystemKV.key == key))
        return row.scalar_one_or_none()


async def _set(key: str, value: str) -> None:
    async with session_scope() as session:
        stmt = sqlite_insert(SystemKV).values(key=key, value=value)
        stmt = stmt.on_conflict_do_update(index_elements=["key"], set_={"value": stmt.excluded.value})
        await session.execute(stmt)


async def is_globally_paused() -> bool:
    return (await _get(PAUSED_KEY) or "0") == "1"


async def set_globally_paused(paused: bool) -> None:
    await _set(PAUSED_KEY, "1" if paused else "0")


async def get_effective_mode() -> str:
    """Return the runtime trade mode: system_kv override if set, else .env value."""
    override = await _get(MODE_KEY)
    if override in OVERRIDE_MODES:
        return override
    return load_settings().trade_mode


async def set_mode_override(mode: str) -> None:
    if mode not in VALID_MODES:
        raise ValueError(f"invalid mode {mode!r}; expected one of {VALID_MODES}")
    if mode == "live":
        if load_settings().trade_mode != "live":
            raise ValueError("live mode can only be enabled with TRADE_MODE=live in .env and a restart")
        await _set(MODE_KEY, "")  # clear the override: fall back to the .env live mode
        return
    await _set(MODE_KEY, mode)
