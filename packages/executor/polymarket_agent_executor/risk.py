"""Risk caps enforced before any decision is committed."""
from __future__ import annotations

import time

from polymarket_agent_core.db import session_scope
from polymarket_agent_core.models import CopyDecision, Follow
from sqlalchemy import func, select


async def check_caps(*, chat_id: int, follow: Follow, intended_size_usdc: float) -> str | None:
    """Return None if OK, else a short reason string for SKIPPED_RISK."""
    if intended_size_usdc > follow.max_per_trade_usd:
        return f"OVER_PER_TRADE({intended_size_usdc:.2f}>{follow.max_per_trade_usd:.2f})"
    cutoff = int(time.time()) - 86400
    async with session_scope() as session:
        stmt = select(func.coalesce(func.sum(CopyDecision.pnl_realized), 0.0)).where(
            CopyDecision.chat_id == chat_id,
            CopyDecision.target_wallet == follow.proxy_wallet,
            CopyDecision.detected_ts >= func.datetime(cutoff, "unixepoch"),
        )
        rolling_pnl = float((await session.execute(stmt)).scalar_one() or 0.0)
    if -rolling_pnl >= follow.daily_loss_cap_usd:
        return f"DAILY_LOSS_CAP_HIT({-rolling_pnl:.2f}>={follow.daily_loss_cap_usd:.2f})"
    return None
