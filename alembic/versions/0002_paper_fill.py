"""paper_fill + system_kv

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-02

"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "paper_fill",
        sa.Column("fill_id", sa.String(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("target_wallet", sa.String(), nullable=False),
        sa.Column("target_tx_hash", sa.String(), nullable=False),
        sa.Column("asset", sa.String(), nullable=False),
        sa.Column("side", sa.String(), nullable=False),
        sa.Column("size", sa.Float(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("usdc_size", sa.Float(), nullable=False),
        sa.Column("condition_id", sa.String()),
        sa.Column("title", sa.String()),
        sa.Column("slug", sa.String()),
        sa.Column("event_slug", sa.String()),
        sa.Column("timestamp", sa.BigInteger(), nullable=False),
        sa.Column(
            "filled_ts",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.PrimaryKeyConstraint("fill_id"),
    )
    op.create_index("ix_paper_fill_chat_ts", "paper_fill", ["chat_id", "timestamp"])

    op.create_table(
        "system_kv",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("system_kv")
    op.drop_index("ix_paper_fill_chat_ts", table_name="paper_fill")
    op.drop_table("paper_fill")
