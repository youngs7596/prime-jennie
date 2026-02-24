"""Fix stock_disclosures legacy column names.

레거시 테이블(대문자, 다른 컬럼명)이 마이그레이션 003 전에 이미 존재하여
모델(StockDisclosureDB)과 컬럼명 불일치 발생:
  - REPORT_CODE → report_type
  - COMPANY_NAME → corp_name

Revision ID: 004
Revises: 003
Create Date: 2026-02-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: str | None = "003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # REPORT_CODE varchar(10) → report_type varchar(50)
    op.alter_column(
        "stock_disclosures",
        "REPORT_CODE",
        new_column_name="report_type",
        type_=sa.String(50),
        existing_type=sa.String(10),
        existing_nullable=True,
    )
    # COMPANY_NAME varchar(255) → corp_name varchar(100)
    # max(length) = 33, 안전하게 축소 가능
    op.alter_column(
        "stock_disclosures",
        "COMPANY_NAME",
        new_column_name="corp_name",
        type_=sa.String(100),
        existing_type=sa.String(255),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "stock_disclosures",
        "report_type",
        new_column_name="REPORT_CODE",
        type_=sa.String(10),
        existing_type=sa.String(50),
        existing_nullable=True,
    )
    op.alter_column(
        "stock_disclosures",
        "corp_name",
        new_column_name="COMPANY_NAME",
        type_=sa.String(255),
        existing_type=sa.String(100),
        existing_nullable=True,
    )
