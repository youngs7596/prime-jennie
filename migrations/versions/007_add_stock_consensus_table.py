"""Add stock_consensus table for forward consensus data.

컨센서스 Forward PER/EPS/ROE, 목표주가, 애널리스트 수 저장.
PK: (stock_code, trade_date) — 히스토리 자동 축적.

Revision ID: 007
Revises: 006
Create Date: 2026-02-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "007"
down_revision: str | None = "006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "stock_consensus",
        sa.Column("stock_code", sa.String(10), sa.ForeignKey("stock_masters.stock_code"), primary_key=True),
        sa.Column("trade_date", sa.Date, primary_key=True),
        sa.Column("forward_per", sa.Float, nullable=True),
        sa.Column("forward_eps", sa.Float, nullable=True),
        sa.Column("forward_roe", sa.Float, nullable=True),
        sa.Column("target_price", sa.Integer, nullable=True),
        sa.Column("analyst_count", sa.Integer, nullable=True),
        sa.Column("investment_opinion", sa.Float, nullable=True),
        sa.Column("source", sa.String(20), nullable=False, server_default="FNGUIDE"),
    )
    op.create_index("ix_consensus_date", "stock_consensus", ["trade_date"])


def downgrade() -> None:
    op.drop_index("ix_consensus_date", table_name="stock_consensus")
    op.drop_table("stock_consensus")
