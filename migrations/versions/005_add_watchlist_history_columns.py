"""Add quant_score, sector_group, market_regime to watchlist_histories.

백테스트/분석용 컬럼 보강:
  - quant_score: Quant vs LLM 성과 비교
  - sector_group: 섹터별 워치리스트 분포 분석
  - market_regime: 국면별 워치리스트 성과 분석

Revision ID: 005
Revises: 004
Create Date: 2026-02-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "005"
down_revision: str | None = "004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("watchlist_histories", sa.Column("quant_score", sa.Float(), nullable=True))
    op.add_column("watchlist_histories", sa.Column("sector_group", sa.String(30), nullable=True))
    op.add_column("watchlist_histories", sa.Column("market_regime", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("watchlist_histories", "market_regime")
    op.drop_column("watchlist_histories", "sector_group")
    op.drop_column("watchlist_histories", "quant_score")
