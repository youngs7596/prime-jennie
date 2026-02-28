"""Add index_daily_prices table for KOSPI/KOSDAQ OHLCV history.

Council 기술 지표(MA/BB/RSI) 계산을 위한 지수 일봉 시계열 테이블.

Revision ID: 009
Revises: 008
Create Date: 2026-02-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "009"
down_revision: str | None = "008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "index_daily_prices",
        sa.Column("index_code", sa.String(10), nullable=False),
        sa.Column("price_date", sa.Date, nullable=False),
        sa.Column("open_price", sa.Float, nullable=False),
        sa.Column("high_price", sa.Float, nullable=False),
        sa.Column("low_price", sa.Float, nullable=False),
        sa.Column("close_price", sa.Float, nullable=False),
        sa.Column("volume", sa.Integer, nullable=False, server_default="0"),
        sa.Column("change_pct", sa.Float, nullable=True),
        sa.PrimaryKeyConstraint("index_code", "price_date"),
    )
    op.create_index("ix_index_daily_date", "index_daily_prices", ["price_date"])


def downgrade() -> None:
    op.drop_index("ix_index_daily_date", table_name="index_daily_prices")
    op.drop_table("index_daily_prices")
