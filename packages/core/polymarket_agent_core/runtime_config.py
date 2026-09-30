"""Runtime-editable settings stored in system_kv under an `rc:` prefix.

Lets the dashboard Settings tab and Telegram bot override a whitelist of `.env`
defaults without a restart. Effective value = override (if present) else the
default the caller passes from load_settings(). Generic + typed accessors.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .db import session_scope
from .models import SystemKV

_PREFIX = "rc:"


async def _get(key: str) -> str | None:
    async with session_scope() as session:
        row = await session.execute(select(SystemKV.value).where(SystemKV.key == _PREFIX + key))
        return row.scalar_one_or_none()


async def set_value(key: str, value: str) -> None:
    async with session_scope() as session:
        stmt = sqlite_insert(SystemKV).values(key=_PREFIX + key, value=value)
        stmt = stmt.on_conflict_do_update(index_elements=["key"], set_={"value": stmt.excluded.value})
        await session.execute(stmt)


async def all_overrides() -> dict[str, str]:
    async with session_scope() as session:
        rows = list((await session.execute(
            select(SystemKV.key, SystemKV.value).where(SystemKV.key.like(_PREFIX + "%"))
        )).all())
    return {k[len(_PREFIX):]: v for k, v in rows}


async def get_float(key: str, default: float) -> float:
    v = await _get(key)
    if v is None:
        return default
    try:
        return float(v)
    except ValueError:
        return default


async def get_int(key: str, default: int) -> int:
    v = await _get(key)
    if v is None:
        return default
    try:
        return int(float(v))
    except ValueError:
        return default


async def get_bool(key: str, default: bool) -> bool:
    v = await _get(key)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")
