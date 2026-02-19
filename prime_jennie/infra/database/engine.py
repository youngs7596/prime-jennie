"""SQLModel engine & session factory."""

from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import event
from sqlmodel import Session, create_engine

from prime_jennie.domain.config import get_config


@lru_cache
def get_engine():
    """프로세스 전역 SQLAlchemy Engine (싱글턴).

    테스트에서는 get_engine.cache_clear() 후 재생성.
    """
    config = get_config()
    engine = create_engine(
        config.db.url,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        echo=config.debug,
    )

    # MariaDB/MySQL utf8mb4 강제
    @event.listens_for(engine, "connect")
    def _set_charset(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("SET NAMES utf8mb4")
        cursor.close()

    return engine


def get_session() -> Generator[Session, None, None]:
    """FastAPI Depends()용 세션 팩토리.

    Usage:
        @app.get("/stocks")
        def list_stocks(session: Session = Depends(get_session)):
            ...
    """
    engine = get_engine()
    with Session(engine) as session:
        yield session
