"""Add stock_disclosures and stock_minute_prices tables.

Revision ID: 003
Revises: 002
Create Date: 2026-02-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: str | None = "002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "stock_disclosures",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("stock_code", sa.String(10), sa.ForeignKey("stock_masters.stock_code"), nullable=False),
        sa.Column("disclosure_date", sa.Date, nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("report_type", sa.String(50), nullable=True),
        sa.Column("receipt_no", sa.String(20), nullable=False),
        sa.Column("corp_name", sa.String(100), nullable=True),
        sa.UniqueConstraint("receipt_no", name="uq_disclosure_receipt"),
    )
    op.create_index("ix_disclosure_code_date", "stock_disclosures", ["stock_code", "disclosure_date"])

    op.create_table(
        "stock_minute_prices",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("stock_code", sa.String(10), sa.ForeignKey("stock_masters.stock_code"), nullable=False),
        sa.Column("price_datetime", sa.DateTime, nullable=False),
        sa.Column("open_price", sa.Integer, nullable=False),
        sa.Column("high_price", sa.Integer, nullable=False),
        sa.Column("low_price", sa.Integer, nullable=False),
        sa.Column("close_price", sa.Integer, nullable=False),
        sa.Column("volume", sa.Integer, nullable=False),
        sa.UniqueConstraint("stock_code", "price_datetime", name="uq_minute_code_time"),
    )
    op.create_index("ix_minute_code_time", "stock_minute_prices", ["stock_code", "price_datetime"])


def downgrade() -> None:
    op.drop_index("ix_minute_code_time", table_name="stock_minute_prices")
    op.drop_table("stock_minute_prices")
    op.drop_index("ix_disclosure_code_date", table_name="stock_disclosures")
    op.drop_table("stock_disclosures")
