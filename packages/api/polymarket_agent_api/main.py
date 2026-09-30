"""FastAPI dashboard backend, bound to 127.0.0.1 only. Read + mutate."""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from polymarket_agent_core import heartbeat
from polymarket_agent_core import runtime_config as rc
from polymarket_agent_core.config import load_settings
from polymarket_agent_core.db import session_scope
from polymarket_agent_core.http import PolymarketHttpClient
from polymarket_agent_core.models import (
    CopyDecision,
    Follow,
    LeaderboardSnapshot,
    PaperEquity,
    PaperFill,
)
from polymarket_agent_executor import (
    VALID_MODES,
    VALUE_CHAT_ID,
    copy_trader_positions,
    get_effective_mode,
    is_globally_paused,
    paper_snapshot,
    scan_value_markets,
    set_globally_paused,
    set_mode_override,
)
from polymarket_agent_ingester.activity import fetch_all_wallet_activity
from polymarket_agent_scoring import compute_smart_score
from polymarket_agent_watcher import WalletWatcher
from pydantic import BaseModel, Field
from sqlalchemy import delete, desc, func, select
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert


def _dashboard_path() -> Path:
    here = Path(__file__).resolve()
    for p in [here.parent, *here.parents]:
        candidate = p.parent / "dashboard" / "index.html"
        if candidate.exists():
            return candidate
        candidate = p / "packages" / "dashboard" / "index.html"
        if candidate.exists():
            return candidate
    return Path("packages/dashboard/index.html")


app = FastAPI(title="PolyMarked", version="0.1.0", docs_url="/api/docs")


def _owner_chat_id() -> int:
    return load_settings().telegram_owner_chat_id


# ---- read-only ----------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def root() -> HTMLResponse:
    p = _dashboard_path()
    if not p.exists():
        return HTMLResponse("<h1>PolyMarked</h1><p>Dashboard HTML not found.</p>", status_code=200)
    return HTMLResponse(p.read_text(encoding="utf-8"))


@app.get("/api/v1/health")
async def health() -> dict:
    """Liveness/readiness probe. Returns subsystem heartbeat ages, DB latency,
    paper fill count, and recent error markers for the dashboard + Task Scheduler
    restart-on-failure logic.
    """
    import time
    now_ts = time.time()
    beats = heartbeat.snapshot()
    ages = {k: round(now_ts - v, 2) for k, v in beats.items()}

    # DB latency probe (cheap COUNT)
    paper_fill_count = 0
    db_latency_ms: float | None = None
    db_error: str | None = None
    try:
        t0 = time.perf_counter()
        async with session_scope() as session:
            paper_fill_count = (await session.execute(
                select(func.count()).select_from(PaperFill)
            )).scalar_one() or 0
        db_latency_ms = round((time.perf_counter() - t0) * 1000, 2)
    except Exception as e:
        db_error = str(e)

    # Healthy = watcher beat within 3x its poll interval; bot is optional.
    s = load_settings()
    watcher_max_age = s.watch_poll_interval_seconds * 3
    watcher_age = ages.get("watcher")
    watcher_ok = watcher_age is not None and watcher_age < watcher_max_age
    ok = (db_error is None) and (watcher_age is None or watcher_ok)

    return {
        "ok": ok,
        "trade_mode": await get_effective_mode(),
        "env_trade_mode": s.trade_mode,
        "paused": await is_globally_paused(),
        "now": datetime.now(UTC).isoformat(),
        "heartbeats": {
            "watcher_age_s": watcher_age,
            "watcher_max_age_s": watcher_max_age,
            "watcher_ok": watcher_ok,
            "bot_alert_pump_age_s": ages.get("bot.alert_pump"),
            "all": ages,
        },
        "db": {
            "latency_ms": db_latency_ms,
            "error": db_error,
        },
        "paper_fills": paper_fill_count,
    }


@app.get("/api/v1/news")
async def news() -> dict:
    from polymarket_agent_core.news import fetch_news
    return {"items": await fetch_news()}


@app.get("/api/v1/leaderboard")
async def leaderboard(category: str = "OVERALL", time_period: str = "ALL", limit: int = 25) -> list[dict]:
    async with session_scope() as session:
        latest = (await session.execute(
            select(LeaderboardSnapshot.snapshot_ts)
            .where(LeaderboardSnapshot.category == category, LeaderboardSnapshot.time_period == time_period)
            .order_by(desc(LeaderboardSnapshot.snapshot_ts))
            .limit(1)
        )).scalar_one_or_none()
        if latest is None:
            return []
        rows = list((await session.execute(
            select(LeaderboardSnapshot)
            .where(
                LeaderboardSnapshot.snapshot_ts == latest,
                LeaderboardSnapshot.category == category,
                LeaderboardSnapshot.time_period == time_period,
            )
            .order_by(LeaderboardSnapshot.rank)
            .limit(limit)
        )).scalars())
    return [
        {
            "rank": r.rank,
            "wallet": r.proxy_wallet,
            "name": r.user_name,
            "pnl": r.pnl,
            "vol": r.vol,
            "verified": r.verified_badge,
        }
        for r in rows
    ]


@app.get("/api/v1/follows")
async def follows() -> list[dict]:
    async with session_scope() as session:
        rows = list((await session.execute(
            select(Follow).order_by(Follow.created_ts)
        )).scalars())
    return [
        {
            "wallet": r.proxy_wallet,
            "nickname": r.nickname,
            "auto_execute": r.auto_execute,
            "paused": r.paused,
            "copy_ratio": r.copy_ratio,
            "max_per_trade_usd": r.max_per_trade_usd,
            "daily_loss_cap_usd": r.daily_loss_cap_usd,
        }
        for r in rows
    ]


@app.get("/api/v1/decisions")
async def decisions(limit: int = 50) -> list[dict]:
    async with session_scope() as session:
        rows = list((await session.execute(
            select(CopyDecision).order_by(desc(CopyDecision.detected_ts)).limit(limit)
        )).scalars())
    return [
        {
            "decision_id": r.decision_id,
            "wallet": r.target_wallet,
            "decision": r.decision,
            "reason": r.reason,
            "intended_size_usd": r.intended_size_usd,
            "intended_price": r.intended_price,
            "intended_side": r.intended_side,
            "detected_ts": r.detected_ts.isoformat() if r.detected_ts else None,
        }
        for r in rows
    ]


def _balance_dict(bal) -> dict:
    realized, unrealized = bal.realized_pnl, bal.unrealized_pnl
    return {
        "fills": bal.fills,
        "realized_pnl": realized,
        "unrealized_pnl": unrealized,
        "total_pnl": realized + unrealized,
        "open_positions": bal.open_positions,
        "starting_bankroll": bal.starting_bankroll,
        "cash": bal.cash,
        "position_value": bal.position_value,
        "total": bal.total,
        "return_pct": ((bal.total - bal.starting_bankroll) / bal.starting_bankroll * 100.0)
        if bal.starting_bankroll else 0.0,
        "marked_live": bal.marked_live,
    }


def _positions_dict(positions, limit: int | None = 100) -> list[dict]:
    rows = positions if limit is None else positions[:limit]
    return [
        {
            "asset": p.asset,
            "title": p.title,
            "event_slug": p.event_slug,
            "size": p.size,
            "avg_entry": p.avg_entry,
            "mark": p.mark,
            "marked_live": p.marked_live,
            "price_source": p.price_source,
            "cost_basis": p.cost_basis,
            "value": p.value,
            "unrealized_pnl": p.unrealized_pnl,
            "unrealized_pct": p.unrealized_pct,
        }
        for p in rows
    ]


@app.get("/api/v1/paper")
async def paper(chat_id: int | None = None) -> dict:
    cid = _owner_chat_id() if chat_id is None else chat_id
    bal, positions = await paper_snapshot(cid)
    async with session_scope() as session:
        recent = list((await session.execute(
            select(PaperFill).where(PaperFill.chat_id == cid)
            .order_by(desc(PaperFill.timestamp)).limit(20)
        )).scalars())
    return {
        "balance": _balance_dict(bal),
        "positions": _positions_dict(positions),
        "recent_fills": [
            {
                "wallet": r.target_wallet,
                "side": r.side,
                "size": r.size,
                "price": r.price,
                "usdc_size": r.usdc_size,
                "title": r.title,
                "event_slug": r.event_slug,
                "ts": r.timestamp,
            }
            for r in recent
        ],
    }


@app.get("/api/v1/positions")
async def positions(chat_id: int | None = None) -> list[dict]:
    cid = _owner_chat_id() if chat_id is None else chat_id
    _, pos = await paper_snapshot(cid)
    return _positions_dict(pos)


@app.get("/api/v1/equity")
async def equity(hours: int = 168, chat_id: int | None = None) -> list[dict]:
    cid = _owner_chat_id() if chat_id is None else chat_id
    since = datetime.now(UTC) - timedelta(hours=max(1, hours))
    async with session_scope() as session:
        rows = list((await session.execute(
            select(PaperEquity)
            .where(PaperEquity.chat_id == cid, PaperEquity.snapshot_ts >= since)
            .order_by(PaperEquity.snapshot_ts)
        )).scalars())
    return [
        {
            "ts": r.snapshot_ts.isoformat(),
            "total": r.total,
            "cash": r.cash,
            "position_value": r.position_value,
            "realized_pnl": r.realized_pnl,
            "unrealized_pnl": r.unrealized_pnl,
            "open_positions": r.open_positions,
            "fills": r.fills,
        }
        for r in rows
    ]


@app.get("/api/v1/stream")
async def stream(chat_id: int | None = None) -> StreamingResponse:
    """Server-Sent Events: pushes the live paper book every ~3s. Powers the
    dashboard's live balance feed without polling."""
    cid = _owner_chat_id() if chat_id is None else chat_id

    async def _gen():
        while True:
            try:
                bal, positions = await paper_snapshot(cid)
                payload = {
                    "balance": _balance_dict(bal),
                    "positions": _positions_dict(positions),
                    "now": datetime.now(UTC).isoformat(),
                }
                yield f"data: {json.dumps(payload)}\n\n"
            except Exception as e:
                yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
            await asyncio.sleep(3.0)

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/v1/mode")
async def get_mode() -> dict:
    return {
        "mode": await get_effective_mode(),
        "env_default": load_settings().trade_mode,
        "paused": await is_globally_paused(),
    }


# Runtime-editable settings (override .env without restart). attr = field on Settings.
_SETTINGS_SPEC = [
    {"key": "min_order_usd", "attr": "min_order_usd", "type": "float",
     "group": "Trading", "label": "Minimum order size ($)",
     "help": "Trades sized below this are skipped entirely."},
    {"key": "max_position_pct", "attr": "max_position_pct", "type": "float",
     "group": "Trading", "label": "Max position (% of bankroll)",
     "help": "Cap any single bet at this % of starting bankroll. 0 = off."},
    {"key": "slippage_bps", "attr": "slippage_bps", "type": "float",
     "group": "Trading", "label": "Slippage (bps)",
     "help": "Haircut on paper fill prices for realism (50 = 0.5% worse fills). Buys higher, sells lower."},
    {"key": "mirror_exits", "attr": "mirror_exits", "type": "bool",
     "group": "Trading", "label": "Mirror exits (copy sells)",
     "help": "When a followed wallet sells a position you hold, close yours too."},
    {"key": "mark_max_tokens", "attr": "mark_max_tokens", "type": "int",
     "group": "Valuation", "label": "Live-mark top N positions",
     "help": "How many of the largest positions to price live / resolve; the rest sit at cost basis."},
    {"key": "active_dust_usd", "attr": "active_dust_usd", "type": "float",
     "group": "Valuation", "label": "Hide positions under ($)",
     "help": "Positions worth less than this are hidden from the active list (still counted in totals)."},
    {"key": "settle_resolved_interval_seconds", "attr": "settle_resolved_interval_seconds", "type": "int",
     "group": "Valuation", "label": "Auto-settle interval (sec, 0=off)",
     "help": "Auto-close resolved positions on this cadence. 0 disables."},
    {"key": "stop_loss_price", "attr": "stop_loss_price", "type": "float",
     "group": "Valuation", "label": "Stop-loss price (0=off)",
     "help": "Auto-close any open position whose live price falls to/under this (e.g. 0.02). Books the loss."},
    {"key": "telegram_alerts_enabled", "attr": "telegram_alerts_enabled", "type": "bool",
     "group": "Telegram", "label": "Telegram profit updates"},
    {"key": "telegram_trade_alerts_enabled", "attr": "telegram_trade_alerts_enabled", "type": "bool",
     "group": "Telegram", "label": "Per-trade alerts (spammy)",
     "help": "DM on every fill. Off by default to avoid spam; profit summaries still send."},
    {"key": "telegram_profit_update_interval_seconds", "attr": "telegram_profit_update_interval_seconds", "type": "int",
     "group": "Telegram", "label": "Profit update interval (sec, 0=off)",
     "help": "DM the owner a live balance summary on this cadence."},
    {"key": "profit_alert_threshold_usd", "attr": "profit_alert_threshold_usd", "type": "float",
     "group": "Telegram", "label": "Profit-move alert ($, 0=off)",
     "help": "Also DM when total P&L moves by at least this since the last update."},
    {"key": "value_enabled", "attr": "value_enabled", "type": "bool",
     "group": "Value strategy", "label": "Enable favorite-longshot scanner",
     "help": "Buys strong-favorite tokens (own paper book) the bias underprices. Off = never trades."},
    {"key": "value_scan_interval_seconds", "attr": "value_scan_interval_seconds", "type": "int",
     "group": "Value strategy", "label": "Scan interval (sec, 0=off)",
     "help": "How often to scan open markets for value bets. 0 disables the loop."},
    {"key": "value_buy_low", "attr": "value_buy_low", "type": "float",
     "group": "Value strategy", "label": "Buy band low",
     "help": "Only buy tokens priced at/above this. 0.80 = the cost-surviving, OOS-validated floor."},
    {"key": "value_buy_high", "attr": "value_buy_high", "type": "float",
     "group": "Value strategy", "label": "Buy band high",
     "help": "Upper price cap. 0.92 stays clear of the efficient near-certain (>0.95) zone."},
    {"key": "value_max_spread", "attr": "value_max_spread", "type": "float",
     "group": "Value strategy", "label": "Max spread (price)",
     "help": "Skip a market if best ask - best bid exceeds this. Edge is thin; keep tight (0.02)."},
    {"key": "value_min_liquidity_usd", "attr": "value_min_liquidity_usd", "type": "float",
     "group": "Value strategy", "label": "Min liquidity ($)",
     "help": "Gamma liquidity floor — skip thin books that can't fill realistically."},
    {"key": "value_min_volume24_usd", "attr": "value_min_volume24_usd", "type": "float",
     "group": "Value strategy", "label": "Min 24h volume ($)",
     "help": "Skip markets trading less than this in the last 24h."},
    {"key": "value_order_usd", "attr": "value_order_usd", "type": "float",
     "group": "Value strategy", "label": "Order size ($)",
     "help": "Dollars per value bet (also capped by Max position %)."},
    {"key": "value_max_open", "attr": "value_max_open", "type": "int",
     "group": "Value strategy", "label": "Max open positions",
     "help": "Cap on concurrent open value positions."},
    {"key": "value_scan_limit", "attr": "value_scan_limit", "type": "int",
     "group": "Value strategy", "label": "Markets scanned / pass",
     "help": "How many top-volume open markets to examine each scan."},
    {"key": "value_min_hours_to_end", "attr": "value_min_hours_to_end", "type": "float",
     "group": "Value strategy", "label": "Min hours to resolution",
     "help": "Skip markets resolving sooner than this (little edge left near resolution)."},
    {"key": "sweep_enabled", "attr": "sweep_enabled", "type": "bool",
     "group": "Auto-sweep", "label": "Enable auto-sweep",
     "help": "Periodically re-scrape + re-score the leaderboard into the Top-by-Smart-Score list. Ops only — Smart Score doesn't predict copy return (copy_latency.py), so it just keeps the ranking fresh."},
    {"key": "sweep_interval_seconds", "attr": "sweep_interval_seconds", "type": "int",
     "group": "Auto-sweep", "label": "Sweep interval (sec, 0=off)",
     "help": "How often to re-sweep. Slow (~1-2 min/run) — use hours (86400 = daily). 0 disables."},
    {"key": "sweep_candidates", "attr": "sweep_candidates", "type": "int",
     "group": "Auto-sweep", "label": "Candidate pool size",
     "help": "How many leaderboard wallets to score each sweep."},
    {"key": "sweep_top_n", "attr": "sweep_top_n", "type": "int",
     "group": "Auto-sweep", "label": "Keep top N",
     "help": "How many ranked wallets to write to the list."},
    {"key": "sweep_auto_follow", "attr": "sweep_auto_follow", "type": "bool",
     "group": "Auto-sweep", "label": "Auto-follow top (watch-only)",
     "help": "After each sweep, follow (watch, NEVER auto-buy) the top N. Off by default; new follows are watched on the next restart."},
    {"key": "sweep_follow_n", "attr": "sweep_follow_n", "type": "int",
     "group": "Auto-sweep", "label": "Auto-follow how many",
     "help": "Top N to follow when auto-follow is on."},
]


def _coerce(typ: str, raw):
    if typ == "bool":
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    if typ == "int":
        return int(float(raw))
    return float(raw)


@app.get("/api/v1/settings")
async def get_settings() -> dict:
    s = load_settings()
    overrides = await rc.all_overrides()
    items = []
    for spec in _SETTINGS_SPEC:
        env_default = getattr(s, spec["attr"])
        ov = overrides.get(spec["key"])
        effective = _coerce(spec["type"], ov) if ov is not None else env_default
        items.append({
            "key": spec["key"], "type": spec["type"], "group": spec["group"],
            "label": spec["label"], "help": spec.get("help", ""),
            "value": effective, "env_default": env_default, "overridden": ov is not None,
        })
    return {"settings": items}


# ---- mutations ----------------------------------------------------------------

class FollowBody(BaseModel):
    wallet: str
    nickname: str | None = None


class AutoExecBody(BaseModel):
    on: bool


class CapsBody(BaseModel):
    copy_ratio: float | None = None
    max_per_trade_usd: float | None = None
    daily_loss_cap_usd: float | None = None


class ModeBody(BaseModel):
    mode: str = Field(pattern="^(off|paper|live)$")


class ScoreBody(BaseModel):
    wallet: str
    max_rows: int = 3000


async def _insert_follow_row(chat_id: int, wallet: str, nickname: str | None) -> None:
    s = load_settings()
    async with session_scope() as session:
        stmt = sqlite_insert(Follow).values(
            chat_id=chat_id,
            proxy_wallet=wallet,
            nickname=nickname,
            copy_ratio=s.default_copy_ratio,
            max_per_trade_usd=s.default_max_per_trade_usd,
            daily_loss_cap_usd=s.default_daily_loss_cap_usd,
        ).on_conflict_do_update(
            index_elements=["chat_id", "proxy_wallet"],
            set_={"nickname": nickname, "paused": False},
        )
        await session.execute(stmt)


@app.post("/api/v1/follow")
async def add_follow(body: FollowBody) -> dict:
    chat_id = _owner_chat_id()
    wallet = body.wallet.strip().lower()
    if not wallet.startswith("0x") or len(wallet) != 42:
        raise HTTPException(400, "wallet must be 0x + 40 hex chars")
    await _insert_follow_row(chat_id, wallet, body.nickname)
    import asyncio as _aio
    watcher = WalletWatcher(queue=_aio.Queue())
    try:
        backfilled = await watcher.backfill(wallet)
    finally:
        await watcher.aclose()
    # Mirror the trader's CURRENT open positions into the paper book.
    copy = await copy_trader_positions(chat_id, wallet)
    return {"ok": True, "wallet": wallet, "backfilled": backfilled,
            "positions_copied": copy.copied, "invested": copy.invested}


@app.post("/api/v1/follow/{wallet}/copy_positions")
async def copy_positions(wallet: str) -> dict:
    """Re-copy a followed trader's current open positions (skips ones already held)."""
    res = await copy_trader_positions(_owner_chat_id(), wallet.lower())
    return {"ok": True, "copied": res.copied, "skipped": res.skipped,
            "invested": res.invested, "cash_left": res.cash_left}


def _top_scores_path() -> Path:
    here = Path(__file__).resolve()
    for p in [*here.parents]:
        cand = p / "data" / "top_scores.json"
        if cand.exists():
            return cand
    return Path("data/top_scores.json")


@app.get("/api/v1/top_scores")
async def top_scores(limit: int = 50) -> dict:
    """Read the scraped Smart-Score ranking (scripts/top_scores.py output)."""
    p = _top_scores_path()
    if not p.exists():
        return {"available": False, "scores": [],
                "hint": "run: uv run python scripts/top_scores.py --candidates 200 --top 50"}
    try:
        rows = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(500, f"could not read top_scores.json: {e}") from e
    return {"available": True, "scores": rows[:max(1, limit)]}


class FollowTopBody(BaseModel):
    n: int = 5


@app.post("/api/v1/follow_top")
async def follow_top(body: FollowTopBody) -> dict:
    """Follow the top-N wallets from the Smart-Score ranking. Watches them (mirrors
    future trades); use Copy positions per wallet to also mirror current holdings."""
    p = _top_scores_path()
    if not p.exists():
        raise HTTPException(400, "no top_scores.json — run scripts/top_scores.py first")
    rows = json.loads(p.read_text(encoding="utf-8"))
    n = max(1, min(body.n, len(rows)))
    chat_id = _owner_chat_id()
    followed = []
    for r in rows[:n]:
        w = (r.get("wallet") or "").lower()
        if w.startswith("0x") and len(w) == 42:
            nick = r.get("name") or f"#{len(followed)+1} score {r.get('smart_score')}"
            await _insert_follow_row(chat_id, w, nick)
            followed.append(w)
    return {"ok": True, "followed": len(followed), "wallets": followed}


class SweepBody(BaseModel):
    candidates: int = 200
    top: int = 50
    max_rows: int = 2000


@app.post("/api/v1/sweep")
async def sweep(body: SweepBody) -> dict:
    """Re-scrape + re-score the leaderboard now and write data/top_scores.json (the
    Top-by-Smart-Score panel + /follow_top source). Slow (~1-2 min). OPS ONLY —
    Smart Score does not predict copy return (scripts/copy_latency.py)."""
    from polymarket_agent_core.http import PolymarketHttpClient
    from polymarket_agent_ingester.sweep import sweep_top_scores, write_top_scores
    async with PolymarketHttpClient() as client:
        rows = await sweep_top_scores(
            client, candidates=body.candidates, top=body.top, max_rows=body.max_rows)
    if not rows:
        raise HTTPException(502, "leaderboard returned no candidates")
    path = write_top_scores(rows)
    return {"ok": True, "ranked": len(rows), "path": str(path)}


def _value_result(res) -> dict:
    return {
        "ok": True,
        "scanned": res.scanned,
        "opened": res.opened,
        "skipped": res.skipped,
        "invested": res.invested,
        "cash_left": res.cash_left,
        "bets": res.opened_bets,
        "value_chat_id": VALUE_CHAT_ID,
    }


@app.post("/api/v1/value/preview")
async def value_preview() -> dict:
    """Dry-run the favorite-longshot scanner: which in-band markets it WOULD buy
    right now (no fills written). Use to sanity-check the band/spread settings."""
    return _value_result(await scan_value_markets(commit=False))


@app.post("/api/v1/value/scan")
async def value_scan() -> dict:
    """Run one favorite-longshot scan now and write fills into the value book
    (chat_id = -1). The supervisor also runs this on value_scan_interval_seconds."""
    return _value_result(await scan_value_markets(commit=True))


@app.delete("/api/v1/follow/{wallet}")
async def del_follow(wallet: str) -> dict:
    chat_id = _owner_chat_id()
    async with session_scope() as session:
        await session.execute(
            delete(Follow).where(Follow.chat_id == chat_id, Follow.proxy_wallet == wallet.lower())
        )
    return {"ok": True}


@app.post("/api/v1/follow/{wallet}/mute")
async def mute(wallet: str) -> dict:
    return await _set_paused(wallet, True)


@app.post("/api/v1/follow/{wallet}/unmute")
async def unmute(wallet: str) -> dict:
    return await _set_paused(wallet, False)


async def _set_paused(wallet: str, paused: bool) -> dict:
    chat_id = _owner_chat_id()
    async with session_scope() as session:
        await session.execute(
            sa_update(Follow)
            .where(Follow.chat_id == chat_id, Follow.proxy_wallet == wallet.lower())
            .values(paused=paused)
        )
    return {"ok": True, "paused": paused}


@app.post("/api/v1/follow/{wallet}/auto_execute")
async def auto_execute(wallet: str, body: AutoExecBody) -> dict:
    chat_id = _owner_chat_id()
    async with session_scope() as session:
        await session.execute(
            sa_update(Follow)
            .where(Follow.chat_id == chat_id, Follow.proxy_wallet == wallet.lower())
            .values(auto_execute=body.on)
        )
    return {"ok": True, "auto_execute": body.on}


@app.patch("/api/v1/follow/{wallet}")
async def patch_caps(wallet: str, body: CapsBody) -> dict:
    chat_id = _owner_chat_id()
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        return {"ok": True, "noop": True}
    async with session_scope() as session:
        await session.execute(
            sa_update(Follow)
            .where(Follow.chat_id == chat_id, Follow.proxy_wallet == wallet.lower())
            .values(**updates)
        )
    return {"ok": True, **updates}


@app.post("/api/v1/panic")
async def panic() -> dict:
    chat_id = _owner_chat_id()
    async with session_scope() as session:
        await session.execute(
            sa_update(Follow).where(Follow.chat_id == chat_id).values(auto_execute=False)
        )
    await set_globally_paused(True)
    cancelled: dict | None = None
    if load_settings().polymarket_private_key:
        try:
            from polymarket_agent_executor.clob import cancel_all
            cancelled = await cancel_all()
        except Exception as e:
            cancelled = {"error": str(e)}
    return {"ok": True, "paused": True, "cancel_all": cancelled}


@app.post("/api/v1/unpanic")
async def unpanic() -> dict:
    await set_globally_paused(False)
    return {"ok": True, "paused": False}


@app.post("/api/v1/mode")
async def post_mode(body: ModeBody) -> dict:
    if body.mode not in VALID_MODES:
        raise HTTPException(400, f"mode must be one of {VALID_MODES}")
    try:
        await set_mode_override(body.mode)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "mode": body.mode}


class SettingsBody(BaseModel):
    updates: dict[str, object]


@app.post("/api/v1/settings")
async def post_settings(body: SettingsBody) -> dict:
    spec_by_key = {s["key"]: s for s in _SETTINGS_SPEC}
    applied = {}
    for key, raw in body.updates.items():
        spec = spec_by_key.get(key)
        if spec is None:
            raise HTTPException(400, f"unknown setting {key!r}")
        try:
            val = _coerce(spec["type"], raw)
        except (TypeError, ValueError):
            raise HTTPException(400, f"bad value for {key!r}: {raw!r}") from None
        await rc.set_value(key, str(val).lower() if spec["type"] == "bool" else str(val))
        applied[key] = val
    return {"ok": True, "applied": applied}


class ResetBody(BaseModel):
    confirm: bool = False


@app.post("/api/v1/paper/reset")
async def reset_paper(body: ResetBody, chat_id: int | None = None) -> dict:
    """Wipe the paper book: delete all paper fills + equity snapshots for the owner.
    The book reverts to the starting bankroll with zero positions. Irreversible."""
    if not body.confirm:
        raise HTTPException(400, "confirm=true required to reset the paper balance")
    cid = _owner_chat_id() if chat_id is None else chat_id
    async with session_scope() as session:
        n_fills = (await session.execute(
            select(func.count()).select_from(PaperFill).where(PaperFill.chat_id == cid)
        )).scalar_one() or 0
        await session.execute(delete(PaperFill).where(PaperFill.chat_id == cid))
        await session.execute(delete(PaperEquity).where(PaperEquity.chat_id == cid))
    return {"ok": True, "deleted_fills": n_fills,
            "starting_bankroll": load_settings().paper_starting_bankroll_usd}


@app.post("/api/v1/telegram/test")
async def telegram_test() -> dict:
    """Send the current paper balance summary to the owner chat, to verify the bot."""
    s = load_settings()
    if not s.telegram_bot_token or not s.telegram_owner_chat_id:
        raise HTTPException(400, "TELEGRAM_BOT_TOKEN and TELEGRAM_OWNER_CHAT_ID must be set")
    from polymarket_agent_bot.alerts import format_balance_summary
    from telegram import Bot
    from telegram.constants import ParseMode
    bal, _ = await paper_snapshot(s.telegram_owner_chat_id)
    try:
        await Bot(s.telegram_bot_token).send_message(
            chat_id=s.telegram_owner_chat_id,
            text=format_balance_summary(bal),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
    except Exception as e:
        raise HTTPException(502, f"telegram send failed: {e}") from e
    return {"ok": True}


@app.post("/api/v1/score")
async def score(body: ScoreBody) -> dict:
    wallet = body.wallet.strip().lower()
    if not wallet.startswith("0x") or len(wallet) != 42:
        raise HTTPException(400, "wallet must be 0x + 40 hex chars")
    async with PolymarketHttpClient() as c:
        events = await fetch_all_wallet_activity(c, wallet=wallet, event_type=None, max_rows=body.max_rows)
    report = compute_smart_score(events)
    return {"wallet": wallet, **report.to_dict()}
