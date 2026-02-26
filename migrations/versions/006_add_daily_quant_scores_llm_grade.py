"""Add llm_grade column to daily_quant_scores.

LLM 분석 등급(S/A/B/C/D) 저장용.
기존 llm_reason과 함께 LLM 분석 결과 전량 보존.

Revision ID: 006
Revises: 005
Create Date: 2026-02-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "006"
down_revision: str | None = "005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("daily_quant_scores", sa.Column("llm_grade", sa.String(5), nullable=True))


def downgrade() -> None:
    op.drop_column("daily_quant_scores", "llm_grade")
