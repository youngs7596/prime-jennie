"""Alembic 마이그레이션 환경 설정."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

from prime_jennie.domain.config import get_config
from prime_jennie.infra.database.models import (  # noqa: F401
    ConfigDB,
    DailyAssetSnapshotDB,
    DailyMacroInsightDB,
    DailyQuantScoreDB,
    GlobalMacroSnapshotDB,
    PositionDB,
    StockDailyPriceDB,
    StockFundamentalDB,
    StockInvestorTradingDB,
    StockMasterDB,
    StockNewsSentimentDB,
    TradeLogDB,
    WatchlistHistoryDB,
)

# Alembic Config
config = context.config

# 로깅 설정
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# SQLModel 메타데이터 (autogenerate 대상)
target_metadata = SQLModel.metadata


def get_url() -> str:
    """AppConfig에서 DB URL 가져오기."""
    return get_config().db.url


def run_migrations_offline() -> None:
    """'offline' 모드 — SQL 스크립트만 생성."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """'online' 모드 — DB에 직접 실행."""
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = get_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
