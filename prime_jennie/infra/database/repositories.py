"""공유 DB 쿼리 — 서비스 레이어가 사용하는 Repository 패턴.

모든 쿼리는 SQLModel Session을 받아 순수 함수로 동작.
도메인 모델 변환은 호출자 책임 (Repository는 DB 모델만 반환).
"""

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import desc, update
from sqlmodel import Session, select

from .models import (
    DailyAssetSnapshotDB,
    DailyMacroInsightDB,
    DailyQuantScoreDB,
    PositionDB,
    StockConsensusDB,
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
    def get_stock(session: Session, stock_code: str) -> StockMasterDB | None:
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
    def get_fundamentals(session: Session, stock_code: str) -> StockFundamentalDB | None:
        stmt = (
            select(StockFundamentalDB)
            .where(StockFundamentalDB.stock_code == stock_code)
            .order_by(desc(StockFundamentalDB.trade_date))
            .limit(1)
        )
        return session.exec(stmt).first()

    @staticmethod
    def get_consensus(session: Session, stock_code: str) -> StockConsensusDB | None:
        """최신 컨센서스 데이터 (trade_date DESC)."""
        stmt = (
            select(StockConsensusDB)
            .where(StockConsensusDB.stock_code == stock_code)
            .order_by(desc(StockConsensusDB.trade_date))
            .limit(1)
        )
        return session.exec(stmt).first()

    @staticmethod
    def get_consensus_history(
        session: Session,
        stock_code: str,
        days: int = 30,
    ) -> list[StockConsensusDB]:
        """Earnings Revision 계산용: 최근 N일치 히스토리."""
        cutoff = date.today() - timedelta(days=days)
        stmt = (
            select(StockConsensusDB)
            .where(StockConsensusDB.stock_code == stock_code)
            .where(StockConsensusDB.trade_date >= cutoff)
            .order_by(StockConsensusDB.trade_date)
        )
        return list(session.exec(stmt).all())

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
    def get_position(session: Session, stock_code: str) -> PositionDB | None:
        return session.get(PositionDB, stock_code)

    @staticmethod
    def save_trade_log(session: Session, trade: TradeLogDB) -> None:
        session.add(trade)
        session.commit()

    @staticmethod
    def get_recent_trades(session: Session, days: int = 7) -> list[TradeLogDB]:
        cutoff = date.today() - timedelta(days=days)
        stmt = select(TradeLogDB).where(TradeLogDB.trade_timestamp >= cutoff).order_by(desc(TradeLogDB.trade_timestamp))
        return list(session.exec(stmt).all())

    @staticmethod
    def upsert_position(session: Session, position: PositionDB) -> None:
        """포지션 생성 또는 업데이트 (같은 종목 추가매수 시 평단가 갱신)."""
        existing = session.get(PositionDB, position.stock_code)
        if existing:
            total_qty = existing.quantity + position.quantity
            total_amount = existing.total_buy_amount + position.total_buy_amount
            existing.quantity = total_qty
            existing.total_buy_amount = total_amount
            existing.average_buy_price = total_amount // total_qty
            existing.updated_at = datetime.utcnow()
        else:
            session.add(position)
        session.commit()

    @staticmethod
    def bulk_update_watermarks(session: Session, watermarks: dict[str, int]) -> int:
        """Redis 워터마크 → DB 일괄 동기화 (높은 값만 갱신).

        Args:
            watermarks: {stock_code: watermark_price}

        Returns:
            실제 갱신된 행 수
        """
        if not watermarks:
            return 0

        updated = 0
        for code, wm in watermarks.items():
            pos = session.get(PositionDB, code)
            if pos and (pos.high_watermark is None or wm > pos.high_watermark):
                pos.high_watermark = wm
                pos.updated_at = datetime.utcnow()
                updated += 1
        if updated:
            session.commit()
        return updated

    @staticmethod
    def reduce_position(session: Session, stock_code: str, sell_qty: int) -> None:
        """포지션 수량 감소. 전량 매도 시 삭제."""
        pos = session.get(PositionDB, stock_code)
        if not pos:
            return
        if sell_qty >= pos.quantity:
            session.delete(pos)
        else:
            pos.quantity -= sell_qty
            pos.total_buy_amount = pos.quantity * pos.average_buy_price
            pos.updated_at = datetime.utcnow()
        session.commit()


# ─── Macro ───────────────────────────────────────────────────────


class MacroRepository:
    """매크로 인사이트."""

    @staticmethod
    def get_latest_insight(session: Session) -> DailyMacroInsightDB | None:
        stmt = select(DailyMacroInsightDB).order_by(desc(DailyMacroInsightDB.insight_date)).limit(1)
        return session.exec(stmt).first()

    @staticmethod
    def get_insight_by_date(session: Session, target_date: date) -> DailyMacroInsightDB | None:
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
            .where(DailyQuantScoreDB.is_active == True)  # noqa: E712
            .order_by(desc(DailyQuantScoreDB.hybrid_score))
        )
        return list(session.exec(stmt).all())

    @staticmethod
    def save_scores(
        session: Session,
        score_date: date,
        run_id: str,
        entries: list[DailyQuantScoreDB],
        *,
        is_active: bool = True,
    ) -> None:
        """run_id 기반 이력 보존: 기존 active 해제 → 새 entries INSERT."""
        if is_active:
            session.exec(
                update(DailyQuantScoreDB)  # type: ignore[call-overload]
                .where(DailyQuantScoreDB.score_date == score_date)
                .where(DailyQuantScoreDB.is_active == True)  # noqa: E712
                .values(is_active=False)
            )
        session.add_all(entries)
        session.commit()


# ─── Watchlist ───────────────────────────────────────────────────


class WatchlistRepository:
    """워치리스트 이력."""

    @staticmethod
    def save_history(
        session: Session,
        snapshot_date: date,
        run_id: str,
        entries: list[WatchlistHistoryDB],
        *,
        is_active: bool = True,
    ) -> None:
        """run_id 기반 이력 보존: 기존 active 해제 → 새 entries INSERT."""
        if is_active:
            session.exec(
                update(WatchlistHistoryDB)  # type: ignore[call-overload]
                .where(WatchlistHistoryDB.snapshot_date == snapshot_date)
                .where(WatchlistHistoryDB.is_active == True)  # noqa: E712
                .values(is_active=False)
            )
        session.add_all(entries)
        session.commit()

    @staticmethod
    def get_latest(session: Session) -> list[WatchlistHistoryDB]:
        """가장 최근 날짜의 active 워치리스트 조회."""
        latest_date_stmt = (
            select(WatchlistHistoryDB.snapshot_date)
            .where(WatchlistHistoryDB.is_active == True)  # noqa: E712
            .order_by(desc(WatchlistHistoryDB.snapshot_date))
            .limit(1)
        )
        latest_date = session.exec(latest_date_stmt).first()
        if not latest_date:
            return []

        stmt = (
            select(WatchlistHistoryDB)
            .where(WatchlistHistoryDB.snapshot_date == latest_date)
            .where(WatchlistHistoryDB.is_active == True)  # noqa: E712
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
    def get_latest(session: Session) -> DailyAssetSnapshotDB | None:
        stmt = select(DailyAssetSnapshotDB).order_by(desc(DailyAssetSnapshotDB.snapshot_date)).limit(1)
        return session.exec(stmt).first()
