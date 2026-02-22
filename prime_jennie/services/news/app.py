"""News Pipeline 서비스 — 뉴스 수집 → 감성 분석 → 벡터 DB 저장.

수집 대상: Watchlist 종목 + 활성 보유 종목.
상시 백그라운드 루프로 자동 수집 + HTTP POST /collect 으로 수동 트리거 가능.

Data Flow:
  Naver Crawl → Redis stream:news:raw → Analyzer (LLM) → DB
                                      → Archiver → Qdrant
"""

import logging
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime

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

_pipeline_status: dict = {
    "last_collect": None,
    "last_analyze": None,
    "last_archive": None,
    "last_collect_count": 0,
    "last_analyze_count": 0,
    "loop_cycle": 0,
    "running": False,
    "daemon_running": False,
}

_loop_running = False
_loop_thread: threading.Thread | None = None

# ─── Constants ──────────────────────────────────────────

INTERVAL_MARKET_SEC = 10 * 60  # 장중 10분
INTERVAL_OFF_SEC = 30 * 60  # 장외 30분
ARCHIVE_EVERY_N = 3  # 3회 수집마다 아카이브 1회


# ─── Helpers ────────────────────────────────────────────


def _load_universe(session: Session) -> dict[str, str]:
    """활성 종목 유니버스 로드 (code→name)."""
    stocks = session.exec(select(StockMasterDB).where(StockMasterDB.is_active)).all()
    return {s.stock_code: s.stock_name for s in stocks}


def _create_session():
    """DB 세션 팩토리."""
    engine = get_engine()
    return Session(engine)


def _is_market_hours() -> bool:
    """장중 시간대 (07:00~16:00)."""
    now = datetime.now()
    return 7 <= now.hour < 16


def _get_interval() -> int:
    return INTERVAL_MARKET_SEC if _is_market_hours() else INTERVAL_OFF_SEC


# ─── Background Loop ──────────────────────────────────


def _news_loop() -> None:
    """상시 수집 루프 — collect → analyze → (주기적) archive."""
    global _loop_running
    cycle = 0
    logger.info("News pipeline daemon started")

    while _loop_running:
        cycle += 1
        _pipeline_status["loop_cycle"] = cycle
        logger.info("[cycle %d] Starting", cycle)
        try:
            session = _create_session()
            try:
                r = get_redis()
                universe = _load_universe(session)

                # Phase 1: Collect
                collector = NewsCollector(r, universe)
                collected = collector.run_once()
                _pipeline_status["last_collect_count"] = collected
                _pipeline_status["last_collect"] = datetime.now().isoformat()
                logger.info("[cycle %d] Collected %d articles", cycle, collected)

                # Phase 2: Analyze
                analyzed = 0
                try:
                    from prime_jennie.infra.llm.factory import LLMFactory

                    get_config()
                    llm = LLMFactory.get_provider("FAST")
                    analyzer = NewsAnalyzer(r, llm, db_session_factory=_create_session)
                    analyzed = analyzer.run_once(max_messages=collected + 50)
                except Exception as e:
                    logger.warning("[cycle %d] Analyzer failed: %s", cycle, e)

                _pipeline_status["last_analyze_count"] = analyzed
                _pipeline_status["last_analyze"] = datetime.now().isoformat()
                logger.info("[cycle %d] Analyzed %d articles", cycle, analyzed)

                # Phase 3: Archive (every N cycles)
                if cycle % ARCHIVE_EVERY_N == 0:
                    try:
                        config = get_config()
                        from .archiver import NewsArchiver

                        archiver = NewsArchiver(
                            r,
                            qdrant_url=config.infra.qdrant_url,
                            embed_url=config.llm.vllm_embed_url,
                        )
                        archived = archiver.run_once()
                        _pipeline_status["last_archive"] = datetime.now().isoformat()
                        logger.info("[cycle %d] Archived %d articles", cycle, archived)
                    except Exception as e:
                        logger.warning("[cycle %d] Archiver failed: %s", cycle, e)

            finally:
                session.close()

        except BaseException as e:
            logger.error("[cycle %d] News loop error: %s", cycle, e, exc_info=True)
            if not isinstance(e, Exception):
                logger.critical("[cycle %d] Non-recoverable error, stopping daemon", cycle)
                break

        # Sleep with early exit check
        interval = _get_interval()
        market = "market" if _is_market_hours() else "off-hours"
        logger.info("[cycle %d] Done. Sleeping %ds (%s)", cycle, interval, market)
        for _ in range(interval):
            if not _loop_running:
                break
            time.sleep(1)

    _pipeline_status["daemon_running"] = False
    logger.info("News pipeline daemon stopped")


# ─── FastAPI App ────────────────────────────────────────


@asynccontextmanager
async def lifespan(app):
    global _loop_running, _loop_thread
    _loop_running = True
    _pipeline_status["daemon_running"] = True
    _loop_thread = threading.Thread(target=_news_loop, name="news-pipeline-loop", daemon=True)
    _loop_thread.start()
    logger.info("News pipeline daemon thread started")

    yield

    _loop_running = False
    _pipeline_status["daemon_running"] = False
    if _loop_thread:
        _loop_thread.join(timeout=10)
    logger.info("News pipeline daemon thread stopped")


app = create_app("news-pipeline", version="1.0.0", lifespan=lifespan, dependencies=["redis", "db"])


class CollectResponse(BaseModel):
    collected: int = 0
    analyzed: int = 0
    message: str = ""


@app.post("/collect")
def trigger_collect(session: Session = Depends(get_db_session)) -> CollectResponse:
    """뉴스 수집 + 감성 분석 파이프라인 수동 실행."""
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

            get_config()
            llm = LLMFactory.get_provider("FAST")
            analyzer = NewsAnalyzer(r, llm, db_session_factory=_create_session)
            analyzed = analyzer.run_once(max_messages=collected + 50)
        except Exception as e:
            logger.warning("Analyzer failed: %s", e)

        _pipeline_status["last_analyze_count"] = analyzed
        _pipeline_status["last_collect"] = datetime.now().isoformat()
        _pipeline_status["last_analyze"] = datetime.now().isoformat()

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

        _pipeline_status["last_analyze"] = datetime.now().isoformat()
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
