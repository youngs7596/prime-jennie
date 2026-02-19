"""Scout Phase 2: 데이터 보강 (Enrichment).

종목별로 KIS 스냅샷, DB 수급/재무 데이터, 뉴스 데이터를 병렬 수집.
에러 격리: 종목별 실패 시 해당 필드만 None (파이프라인 중단 없음).
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Optional

from pydantic import BaseModel
from sqlmodel import Session

from prime_jennie.domain.stock import DailyPrice, StockMaster, StockSnapshot
from prime_jennie.infra.database.repositories import StockRepository
from prime_jennie.infra.kis.client import KISClient

logger = logging.getLogger(__name__)


class InvestorTradingSummary(BaseModel):
    """수급 데이터 요약."""

    foreign_net_buy_sum: float = 0.0
    institution_net_buy_sum: float = 0.0
    foreign_holding_ratio: Optional[float] = None
    foreign_ratio_trend: Optional[float] = None  # 최근 20일 추세


class FinancialTrend(BaseModel):
    """재무 트렌드 데이터."""

    per: Optional[float] = None
    pbr: Optional[float] = None
    roe: Optional[float] = None


class EnrichedCandidate(BaseModel):
    """Phase 2 출력 — 팩터 분석에 필요한 모든 데이터."""

    master: StockMaster
    snapshot: Optional[StockSnapshot] = None
    daily_prices: list[DailyPrice] = []
    news_sentiment_avg: Optional[float] = None  # 최근 뉴스 평균 감성 점수
    investor_trading: Optional[InvestorTradingSummary] = None
    financial_trend: Optional[FinancialTrend] = None


def enrich_candidates(
    candidates: dict[str, StockMaster],
    kis: KISClient,
    session: Session,
    max_workers: int = 8,
) -> dict[str, EnrichedCandidate]:
    """Phase 2: 병렬 데이터 보강.

    Args:
        candidates: {stock_code: StockMaster}
        kis: KIS Gateway 클라이언트
        session: DB 세션
        max_workers: 병렬 워커 수

    Returns:
        {stock_code: EnrichedCandidate}
    """
    codes = list(candidates.keys())
    logger.info("Enriching %d candidates with %d workers", len(codes), max_workers)

    # 1. KIS 스냅샷 병렬 수집
    snapshots = _fetch_snapshots_parallel(codes, kis, max_workers)

    # 2. DB 데이터 수집 (동기)
    result: dict[str, EnrichedCandidate] = {}

    for code in codes:
        master = candidates[code]
        enriched = EnrichedCandidate(master=master)

        # Snapshot
        enriched.snapshot = snapshots.get(code)

        # Daily prices (DB)
        try:
            db_prices = StockRepository.get_daily_prices(session, code, days=150)
            enriched.daily_prices = [
                DailyPrice(
                    stock_code=p.stock_code,
                    price_date=p.price_date,
                    open_price=p.open_price,
                    high_price=p.high_price,
                    low_price=p.low_price,
                    close_price=p.close_price,
                    volume=p.volume,
                    change_pct=p.change_pct,
                )
                for p in db_prices
            ]
        except Exception as e:
            logger.warning("[%s] daily_prices fetch failed: %s", code, e)

        # Investor trading (수급)
        try:
            db_trading = StockRepository.get_investor_trading(session, code, days=60)
            if db_trading:
                foreign_sum = sum(t.foreign_net_buy or 0 for t in db_trading)
                inst_sum = sum(t.institution_net_buy or 0 for t in db_trading)
                latest_ratio = db_trading[-1].foreign_holding_ratio if db_trading else None

                # 외인 비율 추세: 최근 20일 vs 이전 20일
                ratio_trend = None
                ratios = [t.foreign_holding_ratio for t in db_trading if t.foreign_holding_ratio is not None]
                if len(ratios) >= 20:
                    recent = sum(ratios[-10:]) / 10
                    prev = sum(ratios[-20:-10]) / 10
                    ratio_trend = recent - prev

                enriched.investor_trading = InvestorTradingSummary(
                    foreign_net_buy_sum=foreign_sum,
                    institution_net_buy_sum=inst_sum,
                    foreign_holding_ratio=latest_ratio,
                    foreign_ratio_trend=ratio_trend,
                )
        except Exception as e:
            logger.warning("[%s] investor_trading fetch failed: %s", code, e)

        # Fundamentals (재무)
        try:
            fund = StockRepository.get_fundamentals(session, code)
            if fund:
                enriched.financial_trend = FinancialTrend(
                    per=fund.per,
                    pbr=fund.pbr,
                    roe=fund.roe,
                )
        except Exception as e:
            logger.warning("[%s] fundamentals fetch failed: %s", code, e)

        # News sentiment
        try:
            news_rows = StockRepository.get_news_sentiments(session, code, days=14)
            if news_rows:
                enriched.news_sentiment_avg = sum(n.sentiment_score for n in news_rows) / len(news_rows)
        except Exception as e:
            logger.warning("[%s] news_sentiment fetch failed: %s", code, e)

        result[code] = enriched

    logger.info(
        "Enrichment complete: %d candidates, %d with snapshots",
        len(result),
        sum(1 for e in result.values() if e.snapshot is not None),
    )
    return result


def _fetch_snapshots_parallel(
    codes: list[str],
    kis: KISClient,
    max_workers: int,
) -> dict[str, StockSnapshot]:
    """KIS 스냅샷 병렬 수집."""
    snapshots: dict[str, StockSnapshot] = {}

    def fetch_one(code: str) -> tuple[str, Optional[StockSnapshot]]:
        try:
            return code, kis.get_price(code)
        except Exception as e:
            logger.warning("[%s] snapshot failed: %s", code, e)
            return code, None

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(fetch_one, code) for code in codes]
        for future in futures:
            code, snap = future.result()
            if snap:
                snapshots[code] = snap

    return snapshots
