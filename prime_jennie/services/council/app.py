"""Macro Council FastAPI App — 트리거 API + 파이프라인 오케스트레이션."""

import json
import logging
from contextlib import asynccontextmanager
from datetime import date, timedelta

import redis
from sqlmodel import Session

from prime_jennie.domain.config import get_config
from prime_jennie.domain.macro import GlobalSnapshot, MacroInsight, TradingContext
from prime_jennie.infra.database.engine import get_engine
from prime_jennie.infra.database.models import DailyMacroInsightDB
from prime_jennie.infra.database.repositories import MacroRepository
from prime_jennie.infra.redis.cache import TypedCache
from prime_jennie.services.base import create_app

from .pipeline import CouncilInput, CouncilResult, MacroCouncilPipeline
from .telegram_collector import collect_hedgecat_briefing

logger = logging.getLogger(__name__)

# Redis keys
INSIGHT_CACHE_KEY = "macro:daily_insight"
CONTEXT_CACHE_KEY = "macro:trading_context"


@asynccontextmanager
async def lifespan(app):
    """Startup: Redis 연결."""
    config = get_config()
    r = redis.Redis.from_url(config.redis.url, decode_responses=True)
    app.state.redis = r
    app.state.pipeline = MacroCouncilPipeline()
    app.state.insight_cache = TypedCache(r, INSIGHT_CACHE_KEY, MacroInsight, ttl=86400)
    app.state.context_cache = TypedCache(r, CONTEXT_CACHE_KEY, TradingContext, ttl=86400)
    yield
    r.close()


app = create_app("macro-council", version="1.0.0", lifespan=lifespan)


@app.post("/trigger")
async def trigger_council(
    briefing_text: str = "",
    target_date: str | None = None,
):
    """Council 파이프라인 트리거.

    Airflow에서 호출. Redis에서 매크로 스냅샷 + 텔레그램 수집 자동 로드.
    """
    pipeline: MacroCouncilPipeline = app.state.pipeline
    insight_cache: TypedCache = app.state.insight_cache
    r: redis.Redis = app.state.redis

    resolved_date = date.fromisoformat(target_date) if target_date else date.today()

    # Redis에서 수집된 매크로 스냅샷 로드
    global_snapshot = _load_global_snapshot(r, resolved_date)

    # Redis에서 레거시 인사이트 로드 (briefing 보강용)
    legacy_briefing = _load_legacy_insight_as_briefing(r, resolved_date)

    # 텔레그램 브리핑 자동 수집
    telegram_briefing = ""
    try:
        telegram_briefing = await collect_hedgecat_briefing()
    except Exception:
        logger.warning("Telegram collection failed, continuing without it", exc_info=True)

    # 브리핑 텍스트 병합: 텔레그램 + 레거시 인사이트 + 외부 briefing
    combined_briefing = "\n\n".join(filter(None, [telegram_briefing, legacy_briefing, briefing_text]))

    input_data = CouncilInput(
        briefing_text=combined_briefing,
        global_snapshot=global_snapshot,
        target_date=resolved_date,
    )

    result = await pipeline.run(input_data)

    if result.success and result.insight:
        # Redis 저장
        insight_cache.set(result.insight)
        logger.info(
            "Council complete: sentiment=%s, score=%d",
            result.insight.sentiment,
            result.insight.sentiment_score,
        )

        # TradingContext 업데이트
        _update_trading_context(result.insight)

        # DB 영구 저장 (Dashboard 표시용)
        _persist_insight_to_db(result.insight, result, global_snapshot)

        return {
            "status": "success",
            "sentiment": result.insight.sentiment,
            "score": result.insight.sentiment_score,
            "regime_hint": result.insight.regime_hint,
        }

    return {
        "status": "error",
        "error": result.error,
    }


@app.get("/insight")
async def get_insight():
    """최신 인사이트 조회."""
    cache: TypedCache = app.state.insight_cache
    insight = cache.get()
    if insight:
        return insight.model_dump()
    return {"status": "no_data"}


def _load_global_snapshot(r: redis.Redis, target_date: date) -> GlobalSnapshot | None:
    """Redis에서 최신 매크로 스냅샷 로드. target_date부터 7일 전까지 탐색."""
    raw = None
    for days_ago in range(8):
        d = target_date - timedelta(days=days_ago)
        key = f"macro:data:snapshot:{d.isoformat()}"
        raw = r.get(key)
        if raw:
            break
    if not raw:
        logger.warning("No macro snapshot found in Redis: %s", key)
        return None

    try:
        data = json.loads(raw)
        snapshot = GlobalSnapshot(
            snapshot_date=target_date,
            timestamp=data.get("snapshot_time", target_date.isoformat()),
            fed_rate=data.get("fed_rate"),
            treasury_10y=data.get("treasury_10y"),
            us_cpi_yoy=data.get("us_cpi_yoy"),
            vix=data.get("vix"),
            vix_regime=data.get("vix_regime", "normal"),
            dxy_index=data.get("dxy_index"),
            usd_krw=data.get("usd_krw"),
            bok_rate=data.get("bok_rate"),
            kospi_index=data.get("kospi_index"),
            kospi_change_pct=data.get("kospi_change_pct"),
            kosdaq_index=data.get("kosdaq_index"),
            kosdaq_change_pct=data.get("kosdaq_change_pct"),
            kospi_foreign_net=data.get("kospi_foreign_net"),
            kosdaq_foreign_net=data.get("kosdaq_foreign_net"),
            kospi_institutional_net=data.get("kospi_institutional_net"),
            kospi_retail_net=data.get("kospi_retail_net"),
            completeness_pct=data.get("completeness_score", 0) * 100,
            data_sources=data.get("data_sources", []),
        )
        logger.info(
            "Loaded macro snapshot: date=%s, VIX=%.1f, KOSPI=%.0f",
            target_date,
            snapshot.vix or 0,
            snapshot.kospi_index or 0,
        )
        return snapshot
    except Exception:
        logger.exception("Failed to parse macro snapshot from Redis")
        return None


def _load_legacy_insight_as_briefing(r: redis.Redis, target_date: date) -> str:
    """Redis에서 레거시 인사이트(macro:insight:{date})를 브리핑 텍스트로 변환."""
    key = f"macro:insight:{target_date.isoformat()}"
    raw = r.get(key)
    if not raw:
        return ""

    try:
        data = json.loads(raw)
        parts = [f"=== 사전 분석 ({data.get('source_analyst', 'unknown')}) ==="]

        if data.get("regime_hint"):
            parts.append(f"시장 국면: {data['regime_hint']}")
        if data.get("sentiment"):
            parts.append(f"센티먼트: {data['sentiment']} (점수: {data.get('sentiment_score', '?')})")
        if data.get("sector_signals"):
            signals = data["sector_signals"]
            sig_text = ", ".join(f"{k}={v}" for k, v in signals.items()) if isinstance(signals, dict) else str(signals)
            parts.append(f"섹터 신호: {sig_text}")
        if data.get("risk_factors"):
            parts.append("리스크: " + "; ".join(data["risk_factors"][:5]))
        if data.get("opportunity_factors"):
            parts.append("기회: " + "; ".join(data["opportunity_factors"][:5]))

        logger.info("Loaded legacy insight as briefing: date=%s", target_date)
        return "\n".join(parts)
    except Exception:
        logger.warning("Failed to parse legacy insight from Redis", exc_info=True)
        return ""


def _update_trading_context(insight: MacroInsight) -> None:
    """MacroInsight → TradingContext 변환 및 저장."""
    try:
        from prime_jennie.domain.enums import MarketRegime

        # Sentiment → Regime 매핑
        regime_map = {
            "bullish": MarketRegime.STRONG_BULL,
            "neutral_to_bullish": MarketRegime.BULL,
            "neutral": MarketRegime.SIDEWAYS,
            "neutral_to_bearish": MarketRegime.BEAR,
            "bearish": MarketRegime.STRONG_BEAR,
        }
        regime = regime_map.get(insight.sentiment, MarketRegime.SIDEWAYS)

        context = TradingContext(
            date=insight.insight_date,
            market_regime=regime,
            position_multiplier=insight.position_size_pct / 100.0,
            stop_loss_multiplier=insight.stop_loss_adjust_pct / 100.0,
            vix_regime=insight.vix_regime,
            risk_off_level=_calc_risk_off(insight),
            favor_sectors=insight.sectors_to_favor,
            avoid_sectors=insight.sectors_to_avoid,
            is_high_volatility=insight.vix_regime in ("elevated", "crisis"),
        )

        context_cache: TypedCache = app.state.context_cache
        context_cache.set(context)
    except Exception:
        logger.exception("Failed to update trading context")


def _persist_insight_to_db(
    insight: MacroInsight,
    result: CouncilResult,
    global_snapshot: GlobalSnapshot | None,
) -> None:
    """MacroInsight + CouncilResult extras → DB 저장."""
    try:
        db_row = DailyMacroInsightDB(
            insight_date=insight.insight_date,
            sentiment=str(insight.sentiment),
            sentiment_score=insight.sentiment_score,
            regime_hint=insight.regime_hint,
            sectors_to_favor=", ".join(str(s) for s in insight.sectors_to_favor) or None,
            sectors_to_avoid=", ".join(str(s) for s in insight.sectors_to_avoid) or None,
            position_size_pct=insight.position_size_pct,
            stop_loss_adjust_pct=insight.stop_loss_adjust_pct,
            political_risk_level=insight.political_risk_level,
            political_risk_summary=None,
            vix_value=insight.vix_value,
            vix_regime=str(insight.vix_regime) if insight.vix_regime else None,
            usd_krw=insight.usd_krw,
            kospi_index=insight.kospi_index,
            kosdaq_index=insight.kosdaq_index,
            sector_signals_json=json.dumps([s.model_dump() for s in insight.sector_signals], ensure_ascii=False)
            if insight.sector_signals
            else None,
            key_themes_json=json.dumps([t.model_dump() for t in insight.key_themes], ensure_ascii=False)
            if insight.key_themes
            else None,
            risk_factors_json=json.dumps([r.model_dump() for r in insight.risk_factors], ensure_ascii=False)
            if insight.risk_factors
            else None,
            raw_council_output_json=json.dumps(result.raw_outputs, ensure_ascii=False, default=str)
            if result.raw_outputs
            else None,
            council_cost_usd=insight.council_cost_usd,
            # New fields from CouncilResult
            trading_reasoning=result.trading_reasoning or None,
            council_consensus=result.council_consensus or None,
            strategies_to_favor_json=json.dumps(result.strategies_to_favor, ensure_ascii=False)
            if result.strategies_to_favor
            else None,
            strategies_to_avoid_json=json.dumps(result.strategies_to_avoid, ensure_ascii=False)
            if result.strategies_to_avoid
            else None,
            opportunity_factors_json=json.dumps(result.opportunity_factors, ensure_ascii=False)
            if result.opportunity_factors
            else None,
            # Fields from GlobalSnapshot
            kospi_change_pct=global_snapshot.kospi_change_pct if global_snapshot else None,
            kosdaq_change_pct=global_snapshot.kosdaq_change_pct if global_snapshot else None,
            kospi_foreign_net=global_snapshot.kospi_foreign_net if global_snapshot else None,
            kospi_institutional_net=global_snapshot.kospi_institutional_net if global_snapshot else None,
            kospi_retail_net=global_snapshot.kospi_retail_net if global_snapshot else None,
            data_completeness_pct=(
                int(global_snapshot.completeness_pct) if global_snapshot and global_snapshot.completeness_pct else None
            ),
        )

        # political_risk_summary from risk_analyst raw output
        risk_analyst_out = result.raw_outputs.get("risk_analyst", {})
        if risk_analyst_out.get("political_risk_summary"):
            db_row.political_risk_summary = risk_analyst_out["political_risk_summary"]

        engine = get_engine()
        with Session(engine) as session:
            # UPSERT: 같은 날짜면 업데이트
            existing = MacroRepository.get_insight_by_date(session, insight.insight_date)
            if existing:
                for field_name in db_row.model_fields:
                    if field_name == "insight_date":
                        continue
                    setattr(existing, field_name, getattr(db_row, field_name))
                session.commit()
            else:
                MacroRepository.save_insight(session, db_row)

        logger.info("Insight persisted to DB: date=%s", insight.insight_date)
    except Exception:
        logger.exception("Failed to persist insight to DB")


def _calc_risk_off(insight: MacroInsight) -> int:
    """Risk-off level 계산 (0-3). 2개 이상 신호 필요."""
    signals = 0
    if insight.vix_regime in ("elevated", "crisis"):
        signals += 1
    if insight.political_risk_level in ("high", "critical"):
        signals += 1
    if insight.sentiment_score < 30:
        signals += 1
    if insight.position_size_pct < 70:
        signals += 1
    return min(3, max(0, signals - 1))  # 2개부터 risk-off=1
