from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _uuid() -> str:
    return str(uuid.uuid4())


class LeaderboardSnapshot(Base):
    __tablename__ = "leaderboard_snapshot"

    snapshot_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    proxy_wallet: Mapped[str] = mapped_column(String, primary_key=True)
    category: Mapped[str] = mapped_column(String, primary_key=True)
    time_period: Mapped[str] = mapped_column(String, primary_key=True)
    order_by: Mapped[str] = mapped_column(String, primary_key=True)

    rank: Mapped[int | None] = mapped_column(Integer)
    user_name: Mapped[str | None] = mapped_column(String)
    vol: Mapped[float | None] = mapped_column(Float)
    pnl: Mapped[float | None] = mapped_column(Float)
    x_username: Mapped[str | None] = mapped_column(String)
    verified_badge: Mapped[bool | None] = mapped_column(Boolean)
    profile_image: Mapped[str | None] = mapped_column(String)

    __table_args__ = (
        Index("ix_leaderboard_wallet_ts", "proxy_wallet", "snapshot_ts"),
    )


class Wallet(Base):
    __tablename__ = "wallet"

    proxy_wallet: Mapped[str] = mapped_column(String, primary_key=True)
    user_name: Mapped[str | None] = mapped_column(String)
    pseudonym: Mapped[str | None] = mapped_column(String)
    x_username: Mapped[str | None] = mapped_column(String)
    profile_image: Mapped[str | None] = mapped_column(String)
    first_seen_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    last_scored_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ActivityEvent(Base):
    __tablename__ = "activity_event"

    proxy_wallet: Mapped[str] = mapped_column(String, primary_key=True)
    transaction_hash: Mapped[str] = mapped_column(String, primary_key=True)
    asset: Mapped[str] = mapped_column(String, primary_key=True, default="")
    side: Mapped[str] = mapped_column(String, primary_key=True, default="")

    timestamp: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    condition_id: Mapped[str | None] = mapped_column(String)
    size: Mapped[float | None] = mapped_column(Float)
    usdc_size: Mapped[float | None] = mapped_column(Float)
    price: Mapped[float | None] = mapped_column(Float)
    title: Mapped[str | None] = mapped_column(String)
    slug: Mapped[str | None] = mapped_column(String)
    event_slug: Mapped[str | None] = mapped_column(String)
    outcome: Mapped[str | None] = mapped_column(String)
    outcome_index: Mapped[int | None] = mapped_column(Integer)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    ingested_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    __table_args__ = (
        Index("ix_activity_wallet_ts", "proxy_wallet", "timestamp"),
        Index("ix_activity_event_slug", "event_slug"),
    )


class WalletScore(Base):
    __tablename__ = "wallet_score"

    proxy_wallet: Mapped[str] = mapped_column(String, primary_key=True)
    scored_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    smart_score: Mapped[float] = mapped_column(Float, nullable=False)
    tier: Mapped[str] = mapped_column(String, nullable=False)

    profit_factor: Mapped[float | None] = mapped_column(Float)
    sharpe_like: Mapped[float | None] = mapped_column(Float)
    win_rate: Mapped[float | None] = mapped_column(Float)
    max_drawdown: Mapped[float | None] = mapped_column(Float)
    trade_count: Mapped[int | None] = mapped_column(Integer)
    calibration_error: Mapped[float | None] = mapped_column(Float)
    red_flags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    components: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class Follow(Base):
    __tablename__ = "follow"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    proxy_wallet: Mapped[str] = mapped_column(String, primary_key=True)
    nickname: Mapped[str | None] = mapped_column(String)
    auto_execute: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    copy_ratio: Mapped[float] = mapped_column(Float, default=0.01, nullable=False)
    max_per_trade_usd: Mapped[float] = mapped_column(Float, default=25.0, nullable=False)
    daily_loss_cap_usd: Mapped[float] = mapped_column(Float, default=100.0, nullable=False)
    paused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )


class PaperFill(Base):
    __tablename__ = "paper_fill"

    fill_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    target_wallet: Mapped[str] = mapped_column(String, nullable=False)
    target_tx_hash: Mapped[str] = mapped_column(String, nullable=False)
    asset: Mapped[str] = mapped_column(String, nullable=False)
    side: Mapped[str] = mapped_column(String, nullable=False)
    size: Mapped[float] = mapped_column(Float, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    usdc_size: Mapped[float] = mapped_column(Float, nullable=False)
    condition_id: Mapped[str | None] = mapped_column(String)
    title: Mapped[str | None] = mapped_column(String)
    slug: Mapped[str | None] = mapped_column(String)
    event_slug: Mapped[str | None] = mapped_column(String)
    timestamp: Mapped[int] = mapped_column(BigInteger, nullable=False)
    filled_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    __table_args__ = (
        Index("ix_paper_fill_chat_ts", "chat_id", "timestamp"),
    )


class PaperEquity(Base):
    """Periodic snapshot of the paper book's mark-to-market equity, for charting."""

    __tablename__ = "paper_equity"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    snapshot_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    cash: Mapped[float] = mapped_column(Float, nullable=False)
    position_value: Mapped[float] = mapped_column(Float, nullable=False)
    realized_pnl: Mapped[float] = mapped_column(Float, nullable=False)
    unrealized_pnl: Mapped[float] = mapped_column(Float, nullable=False)
    total: Mapped[float] = mapped_column(Float, nullable=False)
    open_positions: Mapped[int] = mapped_column(Integer, nullable=False)
    fills: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        Index("ix_paper_equity_chat_ts", "chat_id", "snapshot_ts"),
    )


class SystemKV(Base):
    __tablename__ = "system_kv"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String, nullable=False)


class CopyDecision(Base):
    __tablename__ = "copy_decision"

    decision_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    target_wallet: Mapped[str] = mapped_column(String, nullable=False)
    target_tx_hash: Mapped[str] = mapped_column(String, nullable=False)
    detected_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    decided_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str | None] = mapped_column(String)
    intended_size_usd: Mapped[float | None] = mapped_column(Float)
    intended_price: Mapped[float | None] = mapped_column(Float)
    intended_side: Mapped[str | None] = mapped_column(String)
    intended_token_id: Mapped[str | None] = mapped_column(String)
    own_tx_hash: Mapped[str | None] = mapped_column(String)
    own_filled_size: Mapped[float | None] = mapped_column(Float)
    own_filled_price: Mapped[float | None] = mapped_column(Float)
    pnl_realized: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (
        Index("ix_copy_decision_chat_detected", "chat_id", "detected_ts"),
    )
