"""Add macro dashboard enhancement fields.

Revision ID: 002
Revises: 001
Create Date: 2026-02-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("daily_macro_insights", sa.Column("trading_reasoning", sa.Text, nullable=True))
    op.add_column("daily_macro_insights", sa.Column("council_consensus", sa.String(30), nullable=True))
    op.add_column("daily_macro_insights", sa.Column("strategies_to_favor_json", sa.Text, nullable=True))
    op.add_column("daily_macro_insights", sa.Column("strategies_to_avoid_json", sa.Text, nullable=True))
    op.add_column("daily_macro_insights", sa.Column("opportunity_factors_json", sa.Text, nullable=True))
    op.add_column("daily_macro_insights", sa.Column("kospi_change_pct", sa.Float, nullable=True))
    op.add_column("daily_macro_insights", sa.Column("kosdaq_change_pct", sa.Float, nullable=True))
    op.add_column("daily_macro_insights", sa.Column("kospi_foreign_net", sa.Float, nullable=True))
    op.add_column("daily_macro_insights", sa.Column("kospi_institutional_net", sa.Float, nullable=True))
    op.add_column("daily_macro_insights", sa.Column("kospi_retail_net", sa.Float, nullable=True))


def downgrade() -> None:
    op.drop_column("daily_macro_insights", "kospi_retail_net")
    op.drop_column("daily_macro_insights", "kospi_institutional_net")
    op.drop_column("daily_macro_insights", "kospi_foreign_net")
    op.drop_column("daily_macro_insights", "kosdaq_change_pct")
    op.drop_column("daily_macro_insights", "kospi_change_pct")
    op.drop_column("daily_macro_insights", "opportunity_factors_json")
    op.drop_column("daily_macro_insights", "strategies_to_avoid_json")
    op.drop_column("daily_macro_insights", "strategies_to_favor_json")
    op.drop_column("daily_macro_insights", "council_consensus")
    op.drop_column("daily_macro_insights", "trading_reasoning")
