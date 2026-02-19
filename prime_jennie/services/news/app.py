"""News Pipeline 서비스 — 뉴스 수집 → 감성 분석 → 벡터 DB 저장.

수집 대상: Watchlist 종목 + 활성 보유 종목.
Airflow DAG 또는 HTTP POST /collect 으로 트리거.

Data Flow:
  Naver Crawl → Redis stream:news:raw → Analyzer (LLM) → DB
                                      → Archiver → Qdrant
"""

import logging
import threading
import time
from contextlib import asynccontextmanager

from fastapi import Depends
from pydantic import BaseModel
from sqlmodel import Session, select

from prime_jennie.domain.config import get_config
from prime_jennie.infra.database.engine import get_engine
from prime_jennie.infra.database.models import StockMasterDB
from prime_jennie.infra.redis.client import get_redis
from prime_jennie.services.base import create_app
from prime_jennie.services.deps import get_db_session

from .analyzer import NewsAnalyzer
from .collector import NewsCollector

logger = logging.getLogger(__name__)

# ─── State ──────────────────────────────────────────────

_pipeline_status = {
    "last_collect": None,
    "last_analyze": None,
    "last_collect_count": 0,
    "last_analyze_count": 0,
    "running": False,
}


# ─── Helpers ────────────────────────────────────────────


def _load_universe(session: Session) -> dict[str, str]:
    """활성 종목 유니버스 로드 (code→name)."""
    stocks = session.exec(
        select(StockMasterDB).where(StockMasterDB.is_active == True)
    ).all()
    return {s.stock_code: s.stock_name for s in stocks}


def _create_session():
    """DB 세션 팩토리."""
    engine = get_engine()
    return Session(engine)


# ─── FastAPI App ────────────────────────────────────────


@asynccontextmanager
async def lifespan(app):
    yield


app = create_app("news-pipeline", version="1.0.0", lifespan=lifespan, dependencies=["redis", "db"])


class CollectResponse(BaseModel):
    collected: int = 0
    analyzed: int = 0
    message: str = ""


@app.post("/collect")
def trigger_collect(session: Session = Depends(get_db_session)) -> CollectResponse:
    """뉴스 수집 + 감성 분석 파이프라인 실행."""
    if _pipeline_status["running"]:
        return CollectResponse(message="Pipeline already running")

    _pipeline_status["running"] = True
    try:
        r = get_redis()
        universe = _load_universe(session)

        # Phase 1: Collect
        collector = NewsCollector(r, universe)
        collected = collector.run_once()
        _pipeline_status["last_collect_count"] = collected

        # Phase 2: Analyze (LLM 감성 분석)
        analyzed = 0
        try:
            from prime_jennie.infra.llm.factory import LLMFactory

            config = get_config()
            llm = LLMFactory.get_provider("FAST")
            analyzer = NewsAnalyzer(r, llm, db_session_factory=_create_session)
            analyzed = analyzer.run_once(max_messages=collected + 50)
        except Exception as e:
            logger.warning("Analyzer failed: %s", e)

        _pipeline_status["last_analyze_count"] = analyzed

        import datetime

        _pipeline_status["last_collect"] = datetime.datetime.now().isoformat()
        _pipeline_status["last_analyze"] = datetime.datetime.now().isoformat()

        return CollectResponse(
            collected=collected,
            analyzed=analyzed,
            message=f"Collected {collected}, analyzed {analyzed} articles",
        )
    finally:
        _pipeline_status["running"] = False


@app.post("/analyze")
def trigger_analyze() -> CollectResponse:
    """감성 분석만 실행 (이미 수집된 뉴스 처리)."""
    if _pipeline_status["running"]:
        return CollectResponse(message="Pipeline already running")

    _pipeline_status["running"] = True
    try:
        r = get_redis()

        from prime_jennie.infra.llm.factory import LLMFactory

        llm = LLMFactory.get_provider("FAST")
        analyzer = NewsAnalyzer(r, llm, db_session_factory=_create_session)
        analyzed = analyzer.run_once(max_messages=500)

        import datetime

        _pipeline_status["last_analyze"] = datetime.datetime.now().isoformat()
        _pipeline_status["last_analyze_count"] = analyzed

        return CollectResponse(analyzed=analyzed, message=f"Analyzed {analyzed} articles")
    except Exception as e:
        return CollectResponse(message=f"Analyze failed: {e}")
    finally:
        _pipeline_status["running"] = False


@app.post("/archive")
def trigger_archive() -> CollectResponse:
    """벡터 DB 아카이빙만 실행."""
    try:
        r = get_redis()
        config = get_config()

        from .archiver import NewsArchiver

        archiver = NewsArchiver(
            r,
            qdrant_url=config.infra.qdrant_url,
            embed_url=config.llm.vllm_embed_url,
        )
        archived = archiver.run_once()

        return CollectResponse(message=f"Archived {archived} articles")
    except Exception as e:
        return CollectResponse(message=f"Archive failed: {e}")


@app.get("/status")
def get_status() -> dict:
    """파이프라인 상태."""
    return _pipeline_status
