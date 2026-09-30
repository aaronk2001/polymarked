"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-05-01

"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "leaderboard_snapshot",
        sa.Column("snapshot_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("proxy_wallet", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("time_period", sa.String(), nullable=False),
        sa.Column("order_by", sa.String(), nullable=False),
        sa.Column("rank", sa.Integer()),
        sa.Column("user_name", sa.String()),
        sa.Column("vol", sa.Float()),
        sa.Column("pnl", sa.Float()),
        sa.Column("x_username", sa.String()),
        sa.Column("verified_badge", sa.Boolean()),
        sa.Column("profile_image", sa.String()),
        sa.PrimaryKeyConstraint(
            "snapshot_ts", "proxy_wallet", "category", "time_period", "order_by"
        ),
    )
    op.create_index(
        "ix_leaderboard_wallet_ts",
        "leaderboard_snapshot",
        ["proxy_wallet", "snapshot_ts"],
    )

    op.create_table(
        "wallet",
        sa.Column("proxy_wallet", sa.String(), nullable=False),
        sa.Column("user_name", sa.String()),
        sa.Column("pseudonym", sa.String()),
        sa.Column("x_username", sa.String()),
        sa.Column("profile_image", sa.String()),
        sa.Column(
            "first_seen_ts",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column("last_scored_ts", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("proxy_wallet"),
    )

    op.create_table(
        "activity_event",
        sa.Column("proxy_wallet", sa.String(), nullable=False),
        sa.Column("transaction_hash", sa.String(), nullable=False),
        sa.Column("asset", sa.String(), nullable=False, server_default=""),
        sa.Column("side", sa.String(), nullable=False, server_default=""),
        sa.Column("timestamp", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("condition_id", sa.String()),
        sa.Column("size", sa.Float()),
        sa.Column("usdc_size", sa.Float()),
        sa.Column("price", sa.Float()),
        sa.Column("title", sa.String()),
        sa.Column("slug", sa.String()),
        sa.Column("event_slug", sa.String()),
        sa.Column("outcome", sa.String()),
        sa.Column("outcome_index", sa.Integer()),
        sa.Column("raw", sa.JSON(), nullable=False),
        sa.Column(
            "ingested_ts",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.PrimaryKeyConstraint(
            "proxy_wallet", "transaction_hash", "asset", "side"
        ),
    )
    op.create_index(
        "ix_activity_wallet_ts",
        "activity_event",
        ["proxy_wallet", "timestamp"],
    )
    op.create_index("ix_activity_event_slug", "activity_event", ["event_slug"])

    op.create_table(
        "wallet_score",
        sa.Column("proxy_wallet", sa.String(), nullable=False),
        sa.Column("scored_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("smart_score", sa.Float(), nullable=False),
        sa.Column("tier", sa.String(), nullable=False),
        sa.Column("profit_factor", sa.Float()),
        sa.Column("sharpe_like", sa.Float()),
        sa.Column("win_rate", sa.Float()),
        sa.Column("max_drawdown", sa.Float()),
        sa.Column("trade_count", sa.Integer()),
        sa.Column("calibration_error", sa.Float()),
        sa.Column("red_flags", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("components", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("proxy_wallet", "scored_ts"),
    )

    op.create_table(
        "follow",
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("proxy_wallet", sa.String(), nullable=False),
        sa.Column("nickname", sa.String()),
        sa.Column("auto_execute", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("copy_ratio", sa.Float(), nullable=False, server_default="0.01"),
        sa.Column("max_per_trade_usd", sa.Float(), nullable=False, server_default="25"),
        sa.Column("daily_loss_cap_usd", sa.Float(), nullable=False, server_default="100"),
        sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_ts",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.PrimaryKeyConstraint("chat_id", "proxy_wallet"),
    )

    op.create_table(
        "copy_decision",
        sa.Column("decision_id", sa.String(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("target_wallet", sa.String(), nullable=False),
        sa.Column("target_tx_hash", sa.String(), nullable=False),
        sa.Column(
            "detected_ts",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column("decided_ts", sa.DateTime(timezone=True)),
        sa.Column("decision", sa.String(), nullable=False),
        sa.Column("reason", sa.String()),
        sa.Column("intended_size_usd", sa.Float()),
        sa.Column("intended_price", sa.Float()),
        sa.Column("intended_side", sa.String()),
        sa.Column("intended_token_id", sa.String()),
        sa.Column("own_tx_hash", sa.String()),
        sa.Column("own_filled_size", sa.Float()),
        sa.Column("own_filled_price", sa.Float()),
        sa.Column("pnl_realized", sa.Float()),
        sa.PrimaryKeyConstraint("decision_id"),
    )
    op.create_index(
        "ix_copy_decision_chat_detected",
        "copy_decision",
        ["chat_id", "detected_ts"],
    )


def downgrade() -> None:
    op.drop_index("ix_copy_decision_chat_detected", table_name="copy_decision")
    op.drop_table("copy_decision")
    op.drop_table("follow")
    op.drop_table("wallet_score")
    op.drop_index("ix_activity_event_slug", table_name="activity_event")
    op.drop_index("ix_activity_wallet_ts", table_name="activity_event")
    op.drop_table("activity_event")
    op.drop_table("wallet")
    op.drop_index("ix_leaderboard_wallet_ts", table_name="leaderboard_snapshot")
    op.drop_table("leaderboard_snapshot")
