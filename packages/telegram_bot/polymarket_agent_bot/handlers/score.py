"""/score, /leaderboard."""
from __future__ import annotations

from polymarket_agent_core.db import session_scope
from polymarket_agent_core.http import PolymarketHttpClient
from polymarket_agent_core.models import LeaderboardSnapshot
from polymarket_agent_ingester.activity import fetch_all_wallet_activity
from polymarket_agent_scoring import compute_smart_score
from sqlalchemy import select
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from ..auth import md2_escape, owner_only


@owner_only
async def score_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if not args:
        await update.effective_message.reply_text("Usage: /score 0x...")
        return
    wallet = args[0].lower()
    msg = await update.effective_message.reply_text("Scoring...")
    async with PolymarketHttpClient() as c:
        events = await fetch_all_wallet_activity(c, wallet=wallet, event_type=None, max_rows=3000)
    report = compute_smart_score(events)
    flags = ", ".join(report.red_flags) or "none"
    body = (
        f"`{md2_escape(wallet)}`\n"
        f"*Score:* {report.smart_score:.1f}  \\({report.tier.upper()}\\)\n"
        f"*Trades:* {report.trade_count}\n"
        f"*Realized PnL:* ${report.realized_pnl_total:,.0f}\n"
        f"*Red flags:* {md2_escape(flags)}"
    )
    await msg.edit_text(body, parse_mode=ParseMode.MARKDOWN_V2)


@owner_only
async def leaderboard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    category = (args[0].upper() if args else "OVERALL")
    async with session_scope() as session:
        snap_ts_q = await session.execute(
            select(LeaderboardSnapshot.snapshot_ts)
            .where(LeaderboardSnapshot.category == category, LeaderboardSnapshot.time_period == "ALL")
            .order_by(LeaderboardSnapshot.snapshot_ts.desc())
            .limit(1)
        )
        latest = snap_ts_q.scalar_one_or_none()
        if latest is None:
            await update.effective_message.reply_text(f"No snapshot for category {category}.")
            return
        rows = list((await session.execute(
            select(LeaderboardSnapshot)
            .where(
                LeaderboardSnapshot.snapshot_ts == latest,
                LeaderboardSnapshot.category == category,
                LeaderboardSnapshot.time_period == "ALL",
            )
            .order_by(LeaderboardSnapshot.rank)
            .limit(10)
        )).scalars())
    lines = [f"*Top 10 {md2_escape(category)} \\(ALL/PNL\\):*"]
    for r in rows:
        nick = md2_escape(r.user_name or r.proxy_wallet[:12] + "...")
        lines.append(f"`{r.rank:>2}` {nick}  ${r.pnl or 0:,.0f}")
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2)
