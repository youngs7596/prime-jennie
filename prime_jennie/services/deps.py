"""FastAPI Depends 기반 DI — 서비스 공통 의존성 팩토리.

Usage:
    from prime_jennie.services.deps import get_db_session, get_redis_client

    @app.get("/stocks")
    def list_stocks(session: Session = Depends(get_db_session)):
        ...
"""

from collections.abc import Generator
from functools import lru_cache

import redis
from sqlmodel import Session

from prime_jennie.domain.config import get_config
from prime_jennie.infra.database.engine import get_engine
from prime_jennie.infra.kis.client import KISClient
from prime_jennie.infra.redis.client import get_redis


def get_db_session() -> Generator[Session, None, None]:
    """요청 스코프 DB 세션 (FastAPI Depends)."""
    engine = get_engine()
    with Session(engine) as session:
        yield session


def get_redis_client() -> redis.Redis:
    """Redis 클라이언트 (싱글턴)."""
    return get_redis()


@lru_cache
def get_kis_client() -> KISClient:
    """KIS Gateway HTTP 클라이언트 (싱글턴)."""
    config = get_config()
    return KISClient(base_url=config.kis.gateway_url)
