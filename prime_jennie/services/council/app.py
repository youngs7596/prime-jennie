"""Macro Council FastAPI App — 트리거 API + 파이프라인 오케스트레이션."""

import logging
from contextlib import asynccontextmanager
from datetime import date

import redis

from prime_jennie.domain.config import get_config
from prime_jennie.domain.macro import MacroInsight, TradingContext
from prime_jennie.infra.redis.cache import TypedCache
from prime_jennie.services.base import create_app

from .pipeline import CouncilInput, MacroCouncilPipeline

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

    Airflow에서 호출. briefing_text는 텔레그램 수집 데이터.
    """
    pipeline: MacroCouncilPipeline = app.state.pipeline
    insight_cache: TypedCache = app.state.insight_cache

    input_data = CouncilInput(
        briefing_text=briefing_text,
        target_date=date.fromisoformat(target_date) if target_date else None,
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
