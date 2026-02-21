"""데이터 로더 — DB 테이블을 메모리에 일괄 로드하여 O(1) lookup 제공."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date

from sqlmodel import Session, select

from prime_jennie.domain.enums import MarketRegime, SectorGroup, TradeTier
from prime_jennie.infra.database.models import (
    DailyMacroInsightDB,
    DailyQuantScoreDB,
    StockDailyPriceDB,
    StockMasterDB,
    WatchlistHistoryDB,
)

from .models import DailyOHLCV, MacroDay, PriceCache, WatchlistEntry

logger = logging.getLogger(__name__)

# Sentiment → MarketRegime (council/app.py:208 재사용)
SENTIMENT_TO_REGIME: dict[str, MarketRegime] = {
    "bullish": MarketRegime.STRONG_BULL,
    "neutral_to_bullish": MarketRegime.BULL,
    "neutral": MarketRegime.SIDEWAYS,
    "neutral_to_bearish": MarketRegime.BEAR,
    "bearish": MarketRegime.STRONG_BEAR,
}


def load_prices(
    session: Session,
    start_date: date,
    end_date: date,
    buffer_days: int = 60,
) -> PriceCache:
    """stock_daily_prices 일괄 로드.

    buffer_days: start_date 이전 데이터도 로드 (ATR, RSI 등 기술 지표용).
    """
    from datetime import timedelta

    buffered_start = start_date - timedelta(days=buffer_days)

    stmt = (
        select(StockDailyPriceDB)
        .where(StockDailyPriceDB.price_date >= buffered_start)
        .where(StockDailyPriceDB.price_date <= end_date)
        .order_by(StockDailyPriceDB.stock_code, StockDailyPriceDB.price_date)
    )
    rows = session.exec(stmt).all()

    cache = PriceCache()
    for row in rows:
        ohlcv = DailyOHLCV(
            price_date=row.price_date,
            open_price=row.open_price,
            high_price=row.high_price,
            low_price=row.low_price,
            close_price=row.close_price,
            volume=row.volume,
        )
        cache.by_stock_date.setdefault(row.stock_code, {})[row.price_date] = ohlcv
        cache.by_stock_sorted.setdefault(row.stock_code, []).append(ohlcv)

    logger.info(
        "Loaded %d price rows for %d stocks (buffer=%d days)",
        len(rows),
        len(cache.by_stock_date),
        buffer_days,
    )
    return cache


def load_watchlists(
    session: Session,
    start_date: date,
    end_date: date,
) -> dict[date, list[WatchlistEntry]]:
    """watchlist_histories 일괄 로드 → {snapshot_date: [WatchlistEntry]} 매핑."""
    stmt = (
        select(WatchlistHistoryDB)
        .where(WatchlistHistoryDB.snapshot_date >= start_date)
        .where(WatchlistHistoryDB.snapshot_date <= end_date)
        .order_by(WatchlistHistoryDB.snapshot_date, WatchlistHistoryDB.rank)
    )
    rows = session.exec(stmt).all()

    # stock_code → sector_group 매핑 로드
    sector_map = _load_sector_map(session)

    result: dict[date, list[WatchlistEntry]] = defaultdict(list)
    for row in rows:
        if not row.is_tradable:
            continue
        tier_str = (row.trade_tier or "TIER2").upper()
        try:
            tier = TradeTier(tier_str)
        except ValueError:
            tier = TradeTier.TIER2
        if tier == TradeTier.BLOCKED:
            continue

        sector = _parse_sector(sector_map.get(row.stock_code))

        entry = WatchlistEntry(
            stock_code=row.stock_code,
            stock_name=row.stock_name,
            snapshot_date=row.snapshot_date,
            hybrid_score=row.hybrid_score or 0.0,
            llm_score=row.llm_score or 0.0,
            trade_tier=tier,
            risk_tag=row.risk_tag or "NEUTRAL",
            rank=row.rank or 99,
            sector_group=sector,
        )
        result[row.snapshot_date].append(entry)

    logger.info(
        "Loaded watchlists for %d dates, total %d entries",
        len(result),
        sum(len(v) for v in result.values()),
    )
    return dict(result)


def load_macro_days(
    session: Session,
    start_date: date,
    end_date: date,
) -> dict[date, MacroDay]:
    """daily_macro_insights 로드 → {insight_date: MacroDay}."""
    stmt = (
        select(DailyMacroInsightDB)
        .where(DailyMacroInsightDB.insight_date >= start_date)
        .where(DailyMacroInsightDB.insight_date <= end_date)
        .order_by(DailyMacroInsightDB.insight_date)
    )
    rows = session.exec(stmt).all()

    result: dict[date, MacroDay] = {}
    for row in rows:
        regime = SENTIMENT_TO_REGIME.get(row.sentiment, MarketRegime.SIDEWAYS)
        result[row.insight_date] = MacroDay(
            insight_date=row.insight_date,
            sentiment=row.sentiment,
            regime=regime,
            position_size_pct=row.position_size_pct,
            stop_loss_adjust_pct=row.stop_loss_adjust_pct,
        )

    logger.info("Loaded macro insights for %d dates", len(result))
    return result


def load_quant_scores(
    session: Session,
    start_date: date,
    end_date: date,
) -> dict[date, dict[str, DailyQuantScoreDB]]:
    """daily_quant_scores 로드 → {score_date: {stock_code: row}}."""
    stmt = (
        select(DailyQuantScoreDB)
        .where(DailyQuantScoreDB.score_date >= start_date)
        .where(DailyQuantScoreDB.score_date <= end_date)
        .order_by(DailyQuantScoreDB.score_date)
    )
    rows = session.exec(stmt).all()

    result: dict[date, dict[str, DailyQuantScoreDB]] = defaultdict(dict)
    for row in rows:
        result[row.score_date][row.stock_code] = row

    logger.info(
        "Loaded quant scores for %d dates, total %d entries",
        len(result),
        sum(len(v) for v in result.values()),
    )
    return dict(result)


def get_trading_dates(price_cache: PriceCache, start_date: date, end_date: date) -> list[date]:
    """가격 데이터에서 실제 거래일 추출 (주말/공휴일 제외)."""
    all_dates: set[date] = set()
    for stock_dates in price_cache.by_stock_date.values():
        for d in stock_dates:
            if start_date <= d <= end_date:
                all_dates.add(d)
    return sorted(all_dates)


# --- Internal helpers ---


def _load_sector_map(session: Session) -> dict[str, str | None]:
    """stock_masters에서 stock_code → sector_group 매핑."""
    stmt = select(StockMasterDB.stock_code, StockMasterDB.sector_group)
    rows = session.exec(stmt).all()
    return {row[0]: row[1] for row in rows}


def _parse_sector(sector_str: str | None) -> SectorGroup | None:
    if not sector_str:
        return None
    try:
        return SectorGroup(sector_str)
    except ValueError:
        return None
