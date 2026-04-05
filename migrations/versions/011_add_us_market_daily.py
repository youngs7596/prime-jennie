"""Add us_market_daily table for US market indicator collection.

미국 주요 지표(SOX, NVDA, S&P 500, 나스닥 선물) 일봉 저장용.
Macro Council 정량 입력 및 KOSPI 상관관계 분석에 활용.

Revision ID: 011
Revises: 010
Create Date: 2026-04-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "011"
down_revision: str | None = "010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "us_market_daily",
        sa.Column("ticker", sa.String(10), nullable=False),
        sa.Column("price_date", sa.Date, nullable=False),
        sa.Column("open_price", sa.Float, nullable=False),
        sa.Column("high_price", sa.Float, nullable=False),
        sa.Column("low_price", sa.Float, nullable=False),
        sa.Column("close_price", sa.Float, nullable=False),
        sa.Column("volume", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("change_pct", sa.Float, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("ticker", "price_date"),
    )
    op.create_index("ix_us_market_daily_date", "us_market_daily", ["price_date"])


def downgrade() -> None:
    op.drop_index("ix_us_market_daily_date", table_name="us_market_daily")
    op.drop_table("us_market_daily")
