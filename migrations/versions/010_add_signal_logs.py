"""Add signal_logs table for backtesting signal history.

Stop 상태에서도 발생한 매수/매도 시그널을 DB에 기록하여
나중에 백테스트 데이터로 활용.

Revision ID: 010
Revises: 009
Create Date: 2026-03-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "010"
down_revision: str | None = "009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "signal_logs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("signal_type", sa.String(10), nullable=False),  # BUY / SELL
        sa.Column("stock_code", sa.String(10), sa.ForeignKey("stock_masters.stock_code"), nullable=False),
        sa.Column("stock_name", sa.String(100), nullable=False),
        sa.Column("strategy", sa.String(50), nullable=True),  # BUY: SignalType, SELL: SellReason
        sa.Column("price", sa.Integer, nullable=False),
        sa.Column("quantity", sa.Integer, nullable=True),  # SELL only
        sa.Column("hybrid_score", sa.Float, nullable=True),  # BUY only
        sa.Column("rsi_value", sa.Float, nullable=True),
        sa.Column("volume_ratio", sa.Float, nullable=True),
        sa.Column("market_regime", sa.String(20), nullable=True),
        sa.Column("position_multiplier", sa.Float, nullable=True),
        sa.Column("profit_pct", sa.Float, nullable=True),  # SELL only
        sa.Column("holding_days", sa.Integer, nullable=True),  # SELL only
        sa.Column("status", sa.String(20), nullable=False),  # suppressed / published
        sa.Column("suppressed_reason", sa.String(100), nullable=True),  # stop / pause 등
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_signal_logs_type_time", "signal_logs", ["signal_type", "created_at"])
    op.create_index("ix_signal_logs_code", "signal_logs", ["stock_code", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_signal_logs_code", table_name="signal_logs")
    op.drop_index("ix_signal_logs_type_time", table_name="signal_logs")
    op.drop_table("signal_logs")
