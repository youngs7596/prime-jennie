"""Macro API — 매크로 인사이트, 시장 국면, 트레이딩 컨텍스트."""

import json
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session

from prime_jennie.domain.enums import MarketRegime, Sentiment, VixRegime
from prime_jennie.domain.macro import MacroInsight, SectorSignal, TradingContext
from prime_jennie.infra.database.repositories import MacroRepository
from prime_jennie.services.deps import get_db_session

router = APIRouter(prefix="/macro", tags=["macro"])


class RegimeResponse(BaseModel):
    """시장 국면 요약."""

    regime: MarketRegime
    position_multiplier: float
    stop_loss_multiplier: float
    risk_off_level: int
    is_high_volatility: bool


@router.get("/insight")
def get_insight(
    target_date: Optional[str] = None,
    session: Session = Depends(get_db_session),
) -> dict:
    """최신 또는 특정 날짜의 매크로 인사이트."""
    if target_date:
        row = MacroRepository.get_insight_by_date(
            session, date.fromisoformat(target_date)
        )
    else:
        row = MacroRepository.get_latest_insight(session)

    if not row:
        return {"status": "no_data"}

    return _db_to_insight_dict(row)


@router.get("/regime")
def get_regime(session: Session = Depends(get_db_session)) -> RegimeResponse:
    """현재 시장 국면 및 트레이딩 컨텍스트."""
    row = MacroRepository.get_latest_insight(session)
    if not row:
        ctx = TradingContext.default()
        return RegimeResponse(
            regime=ctx.market_regime,
            position_multiplier=ctx.position_multiplier,
            stop_loss_multiplier=ctx.stop_loss_multiplier,
            risk_off_level=ctx.risk_off_level,
            is_high_volatility=ctx.is_high_volatility,
        )

    # Sentiment → Regime 매핑
    regime_map = {
        "bullish": MarketRegime.STRONG_BULL,
        "neutral_to_bullish": MarketRegime.BULL,
        "neutral": MarketRegime.SIDEWAYS,
        "neutral_to_bearish": MarketRegime.BEAR,
        "bearish": MarketRegime.STRONG_BEAR,
    }
    regime = regime_map.get(row.sentiment, MarketRegime.SIDEWAYS)
    is_high_vol = row.vix_regime in ("elevated", "crisis") if row.vix_regime else False

    return RegimeResponse(
        regime=regime,
        position_multiplier=row.position_size_pct / 100.0,
        stop_loss_multiplier=row.stop_loss_adjust_pct / 100.0,
        risk_off_level=0,
        is_high_volatility=is_high_vol,
    )


@router.get("/dates")
def get_dates(
    limit: int = 30,
    session: Session = Depends(get_db_session),
) -> list[str]:
    """인사이트가 존재하는 날짜 목록."""
    from sqlalchemy import desc
    from sqlmodel import select

    from prime_jennie.infra.database.models import DailyMacroInsightDB

    stmt = (
        select(DailyMacroInsightDB.insight_date)
        .order_by(desc(DailyMacroInsightDB.insight_date))
        .limit(limit)
    )
    dates = session.exec(stmt).all()
    return [d.isoformat() for d in dates]


def _db_to_insight_dict(row) -> dict:
    """DailyMacroInsightDB → dict 변환."""
    sector_signals = []
    if row.sector_signals_json:
        try:
            sector_signals = json.loads(row.sector_signals_json)
        except json.JSONDecodeError:
            pass

    return {
        "insight_date": row.insight_date.isoformat(),
        "sentiment": row.sentiment,
        "sentiment_score": row.sentiment_score,
        "regime_hint": row.regime_hint,
        "position_size_pct": row.position_size_pct,
        "stop_loss_adjust_pct": row.stop_loss_adjust_pct,
        "political_risk_level": row.political_risk_level,
        "political_risk_summary": row.political_risk_summary,
        "vix_value": row.vix_value,
        "vix_regime": row.vix_regime,
        "usd_krw": row.usd_krw,
        "kospi_index": row.kospi_index,
        "kosdaq_index": row.kosdaq_index,
        "sectors_to_favor": row.sectors_to_favor,
        "sectors_to_avoid": row.sectors_to_avoid,
        "sector_signals": sector_signals,
        "council_cost_usd": row.council_cost_usd,
    }
