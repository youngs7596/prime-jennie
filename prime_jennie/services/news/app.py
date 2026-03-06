"""News Pipeline 서비스 — 뉴스 수집 / 감성 분석 / 벡터 DB 저장.

수집 대상: 활성 종목 유니버스.
3개 독립 스레드가 병렬로 동작:
  - Collector: 주기적 크롤링 → Redis Stream 발행
  - Analyzer: Stream consumer → LLM 감성 분석 → DB 저장
  - Archiver: Stream consumer → Qdrant 임베딩

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
    "collector_cycle": 0,
    "running": False,
    "collector_running": False,
    "analyzer_running": False,
    "archiver_running": False,
}

_loop_running = False
_threads: list[threading.Thread] = []

# ─── Constants ──────────────────────────────────────────

INTERVAL_MARKET_SEC = 10 * 60  # 장중 10분
INTERVAL_OFF_SEC = 30 * 60  # 장외 30분
ANALYZER_BATCH = 50  # 분석기 1회 처리량
ARCHIVER_IDLE_SEC = 10  # 아카이버 대기 시간
ANALYZER_IDLE_SEC = 5  # 분석기 대기 시간
ERROR_BACKOFF_SEC = 30  # 에러 시 대기


# ─── Helpers ────────────────────────────────────────────


def _load_universe(session: Session) -> dict[str, str]:
    """활성 종목 유니버스 로드 (code→name). 우선주(K/L/G suffix) 제외."""
    stocks = session.exec(select(StockMasterDB).where(StockMasterDB.is_active)).all()
    return {s.stock_code: s.stock_name for s in stocks if len(s.stock_code) == 6 and s.stock_code.isdigit()}


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


def _interruptible_sleep(seconds: int) -> None:
    """_loop_running 체크하며 대기."""
    for _ in range(seconds):
        if not _loop_running:
            break
        time.sleep(1)


# ─── Background Threads ──────────────────────────────────


def _collector_loop() -> None:
    """수집 스레드 — 주기적 크롤링 → Stream 발행."""
    cycle = 0
    logger.info("[collector] Thread started")
    _pipeline_status["collector_running"] = True

    while _loop_running:
        cycle += 1
        _pipeline_status["collector_cycle"] = cycle
        try:
            session = _create_session()
            try:
                r = get_redis()
                universe = _load_universe(session)
                collector = NewsCollector(r, universe)
                collected = collector.run_once()
                _pipeline_status["last_collect_count"] = collected
                _pipeline_status["last_collect"] = datetime.now().isoformat()
                logger.info("[collector cycle %d] %d articles", cycle, collected)
            finally:
                session.close()
        except BaseException as e:
            logger.error("[collector] Error: %s", e, exc_info=True)
            if not isinstance(e, Exception):
                break

        interval = _get_interval()
        market = "market" if _is_market_hours() else "off-hours"
        logger.info("[collector cycle %d] Sleeping %ds (%s)", cycle, interval, market)
        _interruptible_sleep(interval)

    _pipeline_status["collector_running"] = False
    logger.info("[collector] Thread stopped")


def _analyzer_loop() -> None:
    """분석 스레드 — Stream consumer → LLM 감성 분석 → DB 저장."""
    logger.info("[analyzer] Thread started")
    _pipeline_status["analyzer_running"] = True
    analyzer = None

    while _loop_running:
        try:
            # lazy init / 에러 복구 시 재생성
            if analyzer is None:
                r = get_redis()
                get_config()
                from prime_jennie.infra.llm.factory import LLMFactory

                llm = LLMFactory.get_provider("FAST")
                analyzer = NewsAnalyzer(r, llm, db_session_factory=_create_session)
                logger.info("[analyzer] Initialized")

            analyzed = analyzer.run_once(max_messages=ANALYZER_BATCH)
            if analyzed > 0:
                _pipeline_status["last_analyze_count"] = analyzed
                _pipeline_status["last_analyze"] = datetime.now().isoformat()
                logger.info("[analyzer] Processed %d articles", analyzed)
            else:
                # Stream에 메시지 없음 — BLOCK_MS(2s) 이미 대기했으므로 짧은 추가 대기
                _interruptible_sleep(ANALYZER_IDLE_SEC)

        except Exception as e:
            logger.warning("[analyzer] Error: %s", e, exc_info=True)
            analyzer = None  # 다음 루프에서 재생성
            _interruptible_sleep(ERROR_BACKOFF_SEC)
        except BaseException:
            break

    _pipeline_status["analyzer_running"] = False
    logger.info("[analyzer] Thread stopped")


def _archiver_loop() -> None:
    """아카이빙 스레드 — Stream consumer → Qdrant 임베딩."""
    logger.info("[archiver] Thread started")
    _pipeline_status["archiver_running"] = True
    archiver = None

    while _loop_running:
        try:
            if archiver is None:
                r = get_redis()
                config = get_config()
                from .archiver import NewsArchiver

                archiver = NewsArchiver(
                    r,
                    qdrant_url=config.infra.qdrant_url,
                    embed_url=config.llm.vllm_embed_url,
                )
                logger.info("[archiver] Initialized")

            archived = archiver.run_once(max_messages=100)
            if archived > 0:
                _pipeline_status["last_archive"] = datetime.now().isoformat()
                logger.info("[archiver] Processed %d articles", archived)
            else:
                _interruptible_sleep(ARCHIVER_IDLE_SEC)

        except Exception as e:
            logger.warning("[archiver] Error: %s", e, exc_info=True)
            archiver = None
            _interruptible_sleep(ERROR_BACKOFF_SEC)
        except BaseException:
            break

    _pipeline_status["archiver_running"] = False
    logger.info("[archiver] Thread stopped")


# ─── FastAPI App ────────────────────────────────────────


@asynccontextmanager
async def lifespan(app):
    global _loop_running, _threads
    _loop_running = True
    _threads = [
        threading.Thread(target=_collector_loop, name="news-collector", daemon=True),
        threading.Thread(target=_analyzer_loop, name="news-analyzer", daemon=True),
        threading.Thread(target=_archiver_loop, name="news-archiver", daemon=True),
    ]
    for t in _threads:
        t.start()
    logger.info("News pipeline: 3 threads started (collector, analyzer, archiver)")

    yield

    _loop_running = False
    for t in _threads:
        t.join(timeout=10)
    _threads.clear()
    logger.info("News pipeline: all threads stopped")


app = create_app("news-pipeline", version="2.0.0", lifespan=lifespan, dependencies=["redis", "db"])


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
