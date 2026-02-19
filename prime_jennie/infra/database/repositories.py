"""공유 DB 쿼리 — 서비스 레이어가 사용하는 Repository 패턴.

모든 쿼리는 SQLModel Session을 받아 순수 함수로 동작.
도메인 모델 변환은 호출자 책임 (Repository는 DB 모델만 반환).
"""

import logging
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import desc
from sqlmodel import Session, select

from .models import (
    DailyAssetSnapshotDB,
    DailyMacroInsightDB,
    DailyQuantScoreDB,
    PositionDB,
    StockDailyPriceDB,
    StockFundamentalDB,
    StockInvestorTradingDB,
    StockMasterDB,
    StockNewsSentimentDB,
    TradeLogDB,
    WatchlistHistoryDB,
)

logger = logging.getLogger(__name__)


# ─── Stock Data ──────────────────────────────────────────────────


class StockRepository:
    """종목 마스터 및 시장 데이터 조회."""

    @staticmethod
    def get_active_stocks(session: Session, market: str | None = None) -> list[StockMasterDB]:
        stmt = select(StockMasterDB).where(StockMasterDB.is_active == True)  # noqa: E712
        if market:
            stmt = stmt.where(StockMasterDB.market == market)
        return list(session.exec(stmt).all())

    @staticmethod
    def get_stock(session: Session, stock_code: str) -> Optional[StockMasterDB]:
        return session.get(StockMasterDB, stock_code)

    @staticmethod
    def get_daily_prices(
        session: Session,
        stock_code: str,
        days: int = 150,
    ) -> list[StockDailyPriceDB]:
        cutoff = date.today() - timedelta(days=days)
        stmt = (
            select(StockDailyPriceDB)
            .where(StockDailyPriceDB.stock_code == stock_code)
            .where(StockDailyPriceDB.price_date >= cutoff)
            .order_by(StockDailyPriceDB.price_date)
        )
        return list(session.exec(stmt).all())

    @staticmethod
    def get_investor_trading(
        session: Session,
        stock_code: str,
        days: int = 60,
    ) -> list[StockInvestorTradingDB]:
        cutoff = date.today() - timedelta(days=days)
        stmt = (
            select(StockInvestorTradingDB)
            .where(StockInvestorTradingDB.stock_code == stock_code)
            .where(StockInvestorTradingDB.trade_date >= cutoff)
            .order_by(StockInvestorTradingDB.trade_date)
        )
        return list(session.exec(stmt).all())

    @staticmethod
    def get_fundamentals(session: Session, stock_code: str) -> Optional[StockFundamentalDB]:
        stmt = (
            select(StockFundamentalDB)
            .where(StockFundamentalDB.stock_code == stock_code)
            .order_by(desc(StockFundamentalDB.trade_date))
            .limit(1)
        )
        return session.exec(stmt).first()

    @staticmethod
    def get_news_sentiments(
        session: Session,
        stock_code: str,
        days: int = 30,
    ) -> list[StockNewsSentimentDB]:
        cutoff = date.today() - timedelta(days=days)
        stmt = (
            select(StockNewsSentimentDB)
            .where(StockNewsSentimentDB.stock_code == stock_code)
            .where(StockNewsSentimentDB.news_date >= cutoff)
            .order_by(desc(StockNewsSentimentDB.news_date))
        )
        return list(session.exec(stmt).all())


# ─── Portfolio ───────────────────────────────────────────────────


class PortfolioRepository:
    """포트폴리오 및 거래 기록."""

    @staticmethod
    def get_positions(session: Session) -> list[PositionDB]:
        return list(session.exec(select(PositionDB)).all())

    @staticmethod
    def get_position(session: Session, stock_code: str) -> Optional[PositionDB]:
        return session.get(PositionDB, stock_code)

    @staticmethod
    def save_trade_log(session: Session, trade: TradeLogDB) -> None:
        session.add(trade)
        session.commit()

    @staticmethod
    def get_recent_trades(session: Session, days: int = 7) -> list[TradeLogDB]:
        cutoff = date.today() - timedelta(days=days)
        stmt = (
            select(TradeLogDB)
            .where(TradeLogDB.trade_timestamp >= cutoff)
            .order_by(desc(TradeLogDB.trade_timestamp))
        )
        return list(session.exec(stmt).all())


# ─── Macro ───────────────────────────────────────────────────────


class MacroRepository:
    """매크로 인사이트."""

    @staticmethod
    def get_latest_insight(session: Session) -> Optional[DailyMacroInsightDB]:
        stmt = select(DailyMacroInsightDB).order_by(desc(DailyMacroInsightDB.insight_date)).limit(1)
        return session.exec(stmt).first()

    @staticmethod
    def get_insight_by_date(session: Session, target_date: date) -> Optional[DailyMacroInsightDB]:
        return session.get(DailyMacroInsightDB, target_date)

    @staticmethod
    def save_insight(session: Session, insight: DailyMacroInsightDB) -> None:
        session.add(insight)
        session.commit()


# ─── Quant Scores ────────────────────────────────────────────────


class QuantScoreRepository:
    """Quant/Hybrid 점수 기록."""

    @staticmethod
    def save_score(session: Session, score: DailyQuantScoreDB) -> None:
        session.add(score)
        session.commit()

    @staticmethod
    def save_scores_bulk(session: Session, scores: list[DailyQuantScoreDB]) -> None:
        session.add_all(scores)
        session.commit()

    @staticmethod
    def get_scores_by_date(session: Session, score_date: date) -> list[DailyQuantScoreDB]:
        stmt = (
            select(DailyQuantScoreDB)
            .where(DailyQuantScoreDB.score_date == score_date)
            .order_by(desc(DailyQuantScoreDB.hybrid_score))
        )
        return list(session.exec(stmt).all())


# ─── Watchlist ───────────────────────────────────────────────────


class WatchlistRepository:
    """워치리스트 이력."""

    @staticmethod
    def save_history(session: Session, entries: list[WatchlistHistoryDB]) -> None:
        session.add_all(entries)
        session.commit()

    @staticmethod
    def get_latest(session: Session) -> list[WatchlistHistoryDB]:
        # 가장 최근 날짜의 워치리스트 조회
        latest_date_stmt = select(WatchlistHistoryDB.snapshot_date).order_by(
            desc(WatchlistHistoryDB.snapshot_date)
        ).limit(1)
        latest_date = session.exec(latest_date_stmt).first()
        if not latest_date:
            return []

        stmt = (
            select(WatchlistHistoryDB)
            .where(WatchlistHistoryDB.snapshot_date == latest_date)
            .order_by(WatchlistHistoryDB.rank)
        )
        return list(session.exec(stmt).all())


# ─── Asset Snapshots ────────────────────────────────────────────


class AssetSnapshotRepository:
    """자산 스냅샷 이력."""

    @staticmethod
    def get_snapshots(session: Session, days: int = 30) -> list[DailyAssetSnapshotDB]:
        cutoff = date.today() - timedelta(days=days)
        stmt = (
            select(DailyAssetSnapshotDB)
            .where(DailyAssetSnapshotDB.snapshot_date >= cutoff)
            .order_by(DailyAssetSnapshotDB.snapshot_date)
        )
        return list(session.exec(stmt).all())

    @staticmethod
    def get_latest(session: Session) -> Optional[DailyAssetSnapshotDB]:
        stmt = (
            select(DailyAssetSnapshotDB)
            .order_by(desc(DailyAssetSnapshotDB.snapshot_date))
            .limit(1)
        )
        return session.exec(stmt).first()
