"""Scout Service — AI 종목 발굴 파이프라인 오케스트레이터.

8단계 파이프라인:
  Phase 1: Universe Loading (DB)
  Phase 2: Data Enrichment (KIS + DB, 병렬)
  Phase 3: Quant Scoring (v2, 잠재력 기반)
  Phase 4: LLM Analysis (Unified Analyst, 1-pass)
  Phase 5: Sector Budget + Watchlist Selection (Greedy)
  Phase 7: Redis watchlist:active 저장
  Phase 8: DB watchlist_histories 저장

Endpoints:
  POST /trigger → 파이프라인 실행
  GET  /status  → 현재 상태
  GET  /health  → HealthStatus
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime

from fastapi import Depends
from pydantic import BaseModel
from sqlmodel import Session

from prime_jennie.domain.config import get_config
from prime_jennie.domain.macro import TradingContext
from prime_jennie.domain.scoring import HybridScore, QuantScore
from prime_jennie.domain.sector import SectorAnalysis, SectorBudget
from prime_jennie.domain.stock import StockMaster
from prime_jennie.domain.watchlist import HotWatchlist
from prime_jennie.infra.database.models import DailyQuantScoreDB, WatchlistHistoryDB
from prime_jennie.infra.database.repositories import QuantScoreRepository, StockRepository, WatchlistRepository
from prime_jennie.infra.llm.factory import LLMFactory
from prime_jennie.infra.redis.client import get_redis
from prime_jennie.services.base import create_app
from prime_jennie.services.deps import get_db_session, get_kis_client

from . import analyst, enrichment, quant, rag_retriever, sector_budget, selection, universe

logger = logging.getLogger(__name__)

# ─── State ───────────────────────────────────────────────────────

_current_phase: str = "idle"
_progress_pct: int = 0
_last_completed_at: datetime | None = None

REDIS_WATCHLIST_KEY = "watchlist:active"
REDIS_WATCHLIST_TTL = 86400  # 24h


# ─── Models ──────────────────────────────────────────────────────


class TriggerRequest(BaseModel):
    source: str = "manual"  # "airflow" | "manual"
    llm_provider: str | None = None  # override: "gemini" | "deepseek_cloud" 등


class TriggerResponse(BaseModel):
    job_id: str
    status: str


class StatusResponse(BaseModel):
    current_phase: str
    progress_pct: int
    last_completed_at: datetime | None


# ─── Lifespan ────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app) -> AsyncIterator[None]:
    logger.info("Scout service starting")
    yield
    logger.info("Scout service shutting down")


# ─── App ─────────────────────────────────────────────────────────

app = create_app("scout-job", version="1.0.0", lifespan=lifespan, dependencies=["redis", "db"])


@app.post("/trigger", response_model=TriggerResponse)
async def trigger(
    body: TriggerRequest,
    session: Session = Depends(get_db_session),
) -> TriggerResponse:
    """Airflow/수동 트리거 → 파이프라인 실행."""
    now = datetime.now(UTC)
    job_id = f"scout-{now.strftime('%Y%m%d-%H%M')}"

    logger.info("Scout triggered: job_id=%s, source=%s", job_id, body.source)

    # 동기 실행 (Airflow 트리거는 완료까지 대기)
    try:
        await run_pipeline(session, run_id=job_id, source=body.source, llm_provider_override=body.llm_provider)
        return TriggerResponse(job_id=job_id, status="completed")
    except Exception as e:
        logger.exception("Scout pipeline failed: %s", e)
        return TriggerResponse(job_id=job_id, status=f"failed: {str(e)[:100]}")


@app.get("/status", response_model=StatusResponse)
async def status() -> StatusResponse:
    """파이프라인 현재 상태."""
    return StatusResponse(
        current_phase=_current_phase,
        progress_pct=_progress_pct,
        last_completed_at=_last_completed_at,
    )


# ─── Pipeline ────────────────────────────────────────────────────


async def run_pipeline(
    session: Session,
    *,
    run_id: str = "",
    source: str = "manual",
    llm_provider_override: str | None = None,
) -> HotWatchlist:
    """7단계 파이프라인 순차 실행."""
    global _current_phase, _progress_pct, _last_completed_at

    config = get_config()
    redis_client = get_redis()
    kis = get_kis_client()

    # --- Phase 0: 이전 워치리스트 읽기 (히스테리시스용) ---
    previous_codes: set[str] = set()
    try:
        prev_raw = redis_client.get(REDIS_WATCHLIST_KEY)
        if prev_raw:
            prev_wl = HotWatchlist.model_validate_json(prev_raw)
            previous_codes = set(prev_wl.stock_codes)
            logger.info("Previous watchlist loaded: %d stocks", len(previous_codes))
    except Exception as e:
        logger.warning("Failed to load previous watchlist: %s", e)

    # --- RAG vectorstore 초기화 ---
    vectorstore = rag_retriever.init_vectorstore()

    # --- Phase 1: Universe ---
    _update_progress("universe", 10)
    candidates = universe.load_universe(session, config.scout)
    if not candidates:
        raise RuntimeError("No candidates in universe")

    # --- Phase 1.5: RAG 후보 발굴 ---
    _update_progress("rag_discovery", 15)
    rag_candidates = rag_retriever.discover_rag_candidates(vectorstore, candidates)
    if rag_candidates:
        _merge_rag_candidates(session, candidates, rag_candidates)

    # --- Phase 2: Enrichment ---
    _update_progress("enrichment", 25)
    enriched = enrichment.enrich_candidates(candidates, kis, session)

    # --- Context 로드 (Phase 3, 4에서 사용) ---
    context = _load_trading_context(redis_client)

    # --- Phase 2.5: 섹터 모멘텀 계산 ---
    sector_returns_20d: dict[str, list[float]] = {}
    for candidate in enriched.values():
        group = candidate.master.sector_group
        if not group or len(candidate.daily_prices) < 20:
            continue
        closes = [p.close_price for p in candidate.daily_prices]
        ret = (closes[-1] / closes[-20] - 1) * 100
        sector_returns_20d.setdefault(group, []).append(ret)

    # 종목 5개 미만 섹터는 대표성 부족 → 제외 (중립 점수 적용)
    sector_avg = {g: sum(r) / len(r) for g, r in sector_returns_20d.items() if len(r) >= 5}
    for candidate in enriched.values():
        group = candidate.master.sector_group
        if group:
            candidate.sector_avg_return_20d = sector_avg.get(group)

    logger.info("Sector 20d returns: %s", {str(g): f"{v:.1f}%" for g, v in sector_avg.items()})

    # --- Phase 2.5: RAG 뉴스 프리페치 ---
    news_cache = rag_retriever.fetch_news_for_stocks(vectorstore, enriched)
    for code, news_text in news_cache.items():
        if code in enriched:
            enriched[code].rag_news_context = news_text

    # --- Phase 2.9: 현재가 하한 필터 ---
    min_price = config.scout.min_price
    before_count = len(enriched)
    low_price_codes = [code for code, c in enriched.items() if c.snapshot and c.snapshot.price < min_price]
    for code in low_price_codes:
        del enriched[code]
    if low_price_codes:
        logger.info("Price filter: %d → %d (min=%s원)", before_count, len(enriched), f"{min_price:,}")

    # --- Phase 3: Quant Scoring ---
    _update_progress("quant_scoring", 45)
    quant_scores: list[QuantScore] = []
    for _code, candidate in enriched.items():
        score = quant.score_candidate(candidate, market_regime=context.market_regime)
        quant_scores.append(score)

    # --- Phase 4: LLM Analysis (병렬) ---
    _update_progress("llm_analysis", 60)
    if llm_provider_override:
        from prime_jennie.infra.llm.factory import _PROVIDER_REGISTRY, _try_import_provider

        if llm_provider_override not in _PROVIDER_REGISTRY:
            _try_import_provider(llm_provider_override)
        provider_cls = _PROVIDER_REGISTRY[llm_provider_override]
        llm_provider = provider_cls()
        logger.info("LLM provider override: %s", llm_provider_override)
    else:
        llm_provider = LLMFactory.get_provider("reasoning")

    # LLM API 동시 호출 제한 (DeepSeek PoC 결과 20이 최적)
    sem = asyncio.Semaphore(20)

    async def _analyze_one(qs: QuantScore) -> HybridScore | None:
        candidate = enriched.get(qs.stock_code)
        if not candidate or qs.total_score < 25:
            return None
        async with sem:
            try:
                return await analyst.run_analyst(qs, candidate, context, llm_provider)
            except Exception as e:
                logger.warning("[%s] LLM analyst failed: %s", qs.stock_code, e)
                return None

    results = await asyncio.gather(*[_analyze_one(qs) for qs in quant_scores])
    hybrid_scores: list[HybridScore] = [r for r in results if r is not None]

    # --- Phase 4.5: MA smoothing ---
    historical = QuantScoreRepository.get_recent_hybrid_scores(session, lookback_dates=config.scout.ma_window - 1)
    ma_map = _compute_ma_scores(hybrid_scores, historical, config.scout.ma_window)
    logger.info("MA scores computed: %d stocks (historical from %d dates)", len(ma_map), len(historical))

    # --- Phase 5: Sector Budget ---
    _update_progress("sector_budget", 80)
    budget = _compute_budget(enriched, context, redis_client)

    # --- Phase 6: Selection (MA scores + hysteresis) ---
    _update_progress("selection", 90)
    watchlist = selection.select_watchlist(
        hybrid_scores,
        enriched,
        budget,
        context,
        max_size=config.scout.max_watchlist_size,
        score_overrides=ma_map,
        previous_codes=previous_codes,
        entry_threshold=config.scout.entry_threshold,
        exit_threshold=config.scout.exit_threshold,
    )

    # --- Phase 6.5: 전 종목 분석 결과 DB 저장 (raw scores 보존) ---
    selected_codes = {e.stock_code for e in watchlist.stocks}
    is_active = source != "test"
    _save_all_scores_to_db(session, quant_scores, hybrid_scores, selected_codes, run_id=run_id, is_active=is_active)

    # --- Phase 7: Save to Redis ---
    _update_progress("saving", 95)
    redis_client.set(
        REDIS_WATCHLIST_KEY,
        watchlist.model_dump_json(),
        ex=REDIS_WATCHLIST_TTL,
    )

    # --- Phase 8: Save to DB ---
    _save_watchlist_to_db(session, watchlist, run_id=run_id, is_active=is_active)

    # --- Phase 9: 오래된 quant scores 정리 ---
    try:
        QuantScoreRepository.cleanup_old_scores(session, config.scout.history_retention_days)
    except Exception:
        logger.exception("Failed to cleanup old scores")

    _update_progress("idle", 100)
    _last_completed_at = datetime.now(UTC)

    logger.info(
        "Scout pipeline completed: %d watchlist stocks, regime=%s",
        len(watchlist.stocks),
        watchlist.market_regime,
    )
    return watchlist


# ─── Helpers ─────────────────────────────────────────────────────


def _update_progress(phase: str, pct: int) -> None:
    global _current_phase, _progress_pct
    _current_phase = phase
    _progress_pct = pct
    logger.info("Pipeline phase: %s (%d%%)", phase, pct)


def _load_trading_context(redis_client) -> TradingContext:
    """Redis에서 트레이딩 컨텍스트 로드 (없으면 안전 기본값)."""
    try:
        raw = redis_client.get("macro:trading_context")
        if raw:
            ctx = TradingContext.model_validate_json(raw)
            logger.info("Trading context loaded: regime=%s", ctx.market_regime)
            return ctx
    except Exception as e:
        logger.warning("Failed to load trading context: %s", e)

    logger.warning("No trading context in Redis, using default (SIDEWAYS)")
    return TradingContext.default()


def _compute_budget(
    enriched: dict[str, enrichment.EnrichedCandidate],
    context: TradingContext,
    redis_client,
) -> SectorBudget | None:
    """섹터 예산 계산."""
    config = get_config()
    if not config.risk.dynamic_sector_budget_enabled:
        return None

    # 간이 섹터 분석: 종목 평균 수익률로 섹터 판단
    sector_returns: dict[str, list[float]] = {}
    for candidate in enriched.values():
        group = candidate.master.sector_group
        if not group:
            continue
        if candidate.snapshot and candidate.snapshot.change_pct:
            sector_returns.setdefault(group.value, []).append(candidate.snapshot.change_pct)

    analyses = []
    for group_str, returns in sector_returns.items():
        avg_ret = sum(returns) / len(returns) if returns else 0.0
        falling_knife = sum(1 for r in returns if r < -5) / len(returns) >= 0.3 if returns else False
        try:
            analyses.append(
                SectorAnalysis(
                    sector_group=group_str,
                    avg_return_pct=avg_ret,
                    stock_count=len(returns),
                    is_falling_knife=falling_knife,
                )
            )
        except ValueError:
            continue

    tiers = sector_budget.assign_sector_tiers(
        analyses,
        council_avoid=context.avoid_sectors or None,
        council_favor=context.favor_sectors or None,
    )

    budget = sector_budget.compute_sector_budget(tiers)
    sector_budget.save_budget_to_redis(budget, redis_client)

    return budget


def _merge_rag_candidates(
    session: Session,
    candidates: dict[str, StockMaster],
    rag_candidates: dict[str, list[str]],
) -> None:
    """RAG 후보를 universe에 병합 (DB에서 StockMaster 조회)."""
    config = get_config().scout
    added = 0
    for code, tags in rag_candidates.items():
        if code in candidates:
            continue
        db_stock = StockRepository.get_stock(session, code)
        if not db_stock or not db_stock.is_active:
            continue
        if (db_stock.market_cap or 0) < config.min_market_cap:
            continue
        try:
            candidates[code] = StockMaster(
                stock_code=db_stock.stock_code,
                stock_name=db_stock.stock_name,
                market=db_stock.market,
                market_cap=db_stock.market_cap,
                sector_naver=db_stock.sector_naver,
                sector_group=db_stock.sector_group,
                is_active=db_stock.is_active,
            )
            added += 1
            logger.info("RAG candidate added: %s (%s) — %s", db_stock.stock_name, code, tags)
        except Exception as e:
            logger.warning("RAG candidate %s skip: %s", code, e)

    if added:
        logger.info("RAG added %d candidates to universe (total: %d)", added, len(candidates))


def _compute_ma_scores(
    hybrid_scores: list[HybridScore],
    historical: dict[str, list[float]],
    window: int = 3,
) -> dict[str, float]:
    """현재 run의 hybrid_score + 과거 scores → 이동평균 계산.

    Returns: {stock_code: ma_score}
    """
    ma_map: dict[str, float] = {}
    for hs in hybrid_scores:
        past = historical.get(hs.stock_code, [])
        all_scores = past + [hs.hybrid_score]
        recent = all_scores[-window:]
        ma = sum(recent) / len(recent)
        ma_map[hs.stock_code] = round(ma, 1)

        if abs(ma - hs.hybrid_score) >= 1.0:
            logger.info(
                "[MA] %s: raw=%.1f, ma=%.1f (window=%d/%d)",
                hs.stock_name,
                hs.hybrid_score,
                ma,
                len(recent),
                window,
            )
    return ma_map


def _save_all_scores_to_db(
    session: Session,
    quant_scores: list[QuantScore],
    hybrid_scores: list[HybridScore],
    selected_codes: set[str],
    *,
    run_id: str = "",
    is_active: bool = True,
) -> None:
    """전 종목 Quant + LLM 분석 결과를 daily_quant_scores에 저장."""
    hybrid_map = {h.stock_code: h for h in hybrid_scores}
    today = date.today()

    entries = []
    for qs in quant_scores:
        hs = hybrid_map.get(qs.stock_code)
        entries.append(
            DailyQuantScoreDB(
                score_date=today,
                stock_code=qs.stock_code,
                stock_name=qs.stock_name,
                total_quant_score=qs.total_score,
                momentum_score=qs.momentum_score,
                quality_score=qs.quality_score,
                value_score=qs.value_score,
                technical_score=qs.technical_score,
                news_score=qs.news_score,
                supply_demand_score=qs.supply_demand_score,
                llm_score=hs.llm_score if hs else None,
                hybrid_score=hs.hybrid_score if hs else None,
                risk_tag=hs.risk_tag.value if hs else None,
                trade_tier=hs.trade_tier.value if hs else None,
                is_tradable=hs.is_tradable if hs else False,
                is_final_selected=qs.stock_code in selected_codes,
                llm_grade=hs.llm_grade if hs else None,
                llm_reason=hs.llm_reason if hs else None,
                run_id=run_id,
                is_active=is_active,
            )
        )

    try:
        QuantScoreRepository.save_scores(session, today, run_id, entries, is_active=is_active)
        logger.info("All scores saved to DB: %d entries (run_id=%s) for %s", len(entries), run_id, today)
    except Exception:
        logger.exception("Failed to save scores to DB")
        session.rollback()


def _save_watchlist_to_db(
    session: Session,
    watchlist: HotWatchlist,
    *,
    run_id: str = "",
    is_active: bool = True,
) -> None:
    """워치리스트 → watchlist_histories 테이블 저장 (run_id 기반 이력 보존)."""
    today = date.today()
    entries = [
        WatchlistHistoryDB(
            snapshot_date=today,
            stock_code=e.stock_code,
            stock_name=e.stock_name,
            llm_score=e.llm_score,
            hybrid_score=e.hybrid_score,
            is_tradable=e.is_tradable,
            trade_tier=e.trade_tier.value if e.trade_tier else None,
            risk_tag=e.risk_tag.value if e.risk_tag else None,
            rank=e.rank,
            quant_score=e.quant_score,
            sector_group=e.sector_group.value if e.sector_group else None,
            market_regime=watchlist.market_regime.value,
            run_id=run_id,
            is_active=is_active,
        )
        for e in watchlist.stocks
    ]
    try:
        WatchlistRepository.save_history(session, today, run_id, entries, is_active=is_active)
        logger.info("Watchlist saved to DB: %d entries (run_id=%s) for %s", len(entries), run_id, today)
    except Exception:
        logger.exception("Failed to save watchlist to DB")
        session.rollback()
