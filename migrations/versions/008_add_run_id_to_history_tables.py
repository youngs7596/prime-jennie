"""Add run_id and is_active to watchlist_histories and daily_quant_scores.

실행 단위(run_id)로 이력 보존, 날짜별 마지막 운영 실행만 is_active=True.

watchlist_histories: PK 변경 (snapshot_date, stock_code) → (snapshot_date, stock_code, run_id)
daily_quant_scores: Unique 변경 (score_date, stock_code) → (score_date, stock_code, run_id)

Revision ID: 008
Revises: 007
Create Date: 2026-02-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "008"
down_revision: str | None = "007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── watchlist_histories ──────────────────────────────────────
    # 1) Add new columns
    op.add_column(
        "watchlist_histories",
        sa.Column("run_id", sa.String(30), nullable=False, server_default=""),
    )
    op.add_column(
        "watchlist_histories",
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("1")),
    )

    # 2) PK 변경: (snapshot_date, stock_code) → (snapshot_date, stock_code, run_id)
    op.drop_constraint("PRIMARY", "watchlist_histories", type_="primary")
    op.create_primary_key(
        "pk_watchlist_histories",
        "watchlist_histories",
        ["snapshot_date", "stock_code", "run_id"],
    )

    # 3) Index for active lookups
    op.create_index(
        "ix_watchlist_active",
        "watchlist_histories",
        ["snapshot_date", "is_active"],
    )

    # ── daily_quant_scores ───────────────────────────────────────
    # 1) Add new columns
    op.add_column(
        "daily_quant_scores",
        sa.Column("run_id", sa.String(30), nullable=False, server_default=""),
    )
    op.add_column(
        "daily_quant_scores",
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("1")),
    )

    # 2) Unique constraint 변경
    op.drop_constraint("uq_quant_date_code", "daily_quant_scores", type_="unique")
    op.create_unique_constraint(
        "uq_quant_date_code_run",
        "daily_quant_scores",
        ["score_date", "stock_code", "run_id"],
    )

    # 3) Index for active lookups
    op.create_index(
        "ix_quant_active",
        "daily_quant_scores",
        ["score_date", "is_active"],
    )


def downgrade() -> None:
    # ── daily_quant_scores ───────────────────────────────────────
    op.drop_index("ix_quant_active", table_name="daily_quant_scores")
    op.drop_constraint("uq_quant_date_code_run", "daily_quant_scores", type_="unique")
    op.create_unique_constraint(
        "uq_quant_date_code",
        "daily_quant_scores",
        ["score_date", "stock_code"],
    )
    op.drop_column("daily_quant_scores", "is_active")
    op.drop_column("daily_quant_scores", "run_id")

    # ── watchlist_histories ──────────────────────────────────────
    op.drop_index("ix_watchlist_active", table_name="watchlist_histories")
    op.drop_constraint("pk_watchlist_histories", "watchlist_histories", type_="primary")
    op.create_primary_key(
        "pk_watchlist_histories",
        "watchlist_histories",
        ["snapshot_date", "stock_code"],
    )
    op.drop_column("watchlist_histories", "is_active")
    op.drop_column("watchlist_histories", "run_id")
