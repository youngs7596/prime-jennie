"""Portfolio API — 포트폴리오 상태, 보유 종목, 자산 히스토리."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session

from prime_jennie.domain.portfolio import DailySnapshot, Position, PortfolioState
from prime_jennie.infra.database.repositories import (
    AssetSnapshotRepository,
    PortfolioRepository,
)
from prime_jennie.services.deps import get_db_session

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


class PerformanceSummary(BaseModel):
    """거래 성과 요약."""

    total_trades: int = 0
    win_trades: int = 0
    loss_trades: int = 0
    win_rate: float = 0.0
    avg_return_pct: float = 0.0
    total_profit: int = 0


@router.get("/summary")
def get_summary(session: Session = Depends(get_db_session)) -> PortfolioState:
    """포트폴리오 전체 요약."""
    positions_db = PortfolioRepository.get_positions(session)
    snapshot = AssetSnapshotRepository.get_latest(session)

    positions = [
        Position(
            stock_code=p.stock_code,
            stock_name=p.stock_name,
            quantity=p.quantity,
            average_buy_price=p.average_buy_price,
            total_buy_amount=p.total_buy_amount,
            sector_group=p.sector_group,
            high_watermark=p.high_watermark,
            stop_loss_price=p.stop_loss_price,
        )
        for p in positions_db
    ]

    stock_eval = sum(p.total_buy_amount for p in positions_db)
    cash = snapshot.cash_balance if snapshot else 0
    total = snapshot.total_asset if snapshot else stock_eval

    return PortfolioState(
        positions=positions,
        cash_balance=cash,
        total_asset=total,
        stock_eval_amount=stock_eval,
        position_count=len(positions),
        timestamp=datetime.now(timezone.utc),
    )


@router.get("/positions")
def get_positions(session: Session = Depends(get_db_session)) -> list[Position]:
    """보유 종목 목록."""
    positions_db = PortfolioRepository.get_positions(session)
    return [
        Position(
            stock_code=p.stock_code,
            stock_name=p.stock_name,
            quantity=p.quantity,
            average_buy_price=p.average_buy_price,
            total_buy_amount=p.total_buy_amount,
            sector_group=p.sector_group,
            high_watermark=p.high_watermark,
            stop_loss_price=p.stop_loss_price,
        )
        for p in positions_db
    ]


@router.get("/history")
def get_history(
    days: int = 30,
    session: Session = Depends(get_db_session),
) -> list[DailySnapshot]:
    """자산 히스토리 (일별 스냅샷)."""
    snapshots = AssetSnapshotRepository.get_snapshots(session, days=days)
    return [
        DailySnapshot(
            snapshot_date=s.snapshot_date,
            total_asset=s.total_asset,
            cash_balance=s.cash_balance,
            stock_eval_amount=s.stock_eval_amount,
            total_profit_loss=s.total_profit_loss,
            realized_profit_loss=s.realized_profit_loss,
        )
        for s in snapshots
    ]


@router.get("/performance")
def get_performance(
    days: int = 30,
    session: Session = Depends(get_db_session),
) -> PerformanceSummary:
    """거래 성과 요약."""
    trades = PortfolioRepository.get_recent_trades(session, days=days)

    sell_trades = [t for t in trades if t.trade_type == "SELL"]
    if not sell_trades:
        return PerformanceSummary()

    wins = [t for t in sell_trades if (t.profit_pct or 0) > 0]
    losses = [t for t in sell_trades if (t.profit_pct or 0) <= 0]
    total_profit = sum(t.profit_amount or 0 for t in sell_trades)
    avg_return = (
        sum(t.profit_pct or 0 for t in sell_trades) / len(sell_trades)
        if sell_trades
        else 0.0
    )

    return PerformanceSummary(
        total_trades=len(sell_trades),
        win_trades=len(wins),
        loss_trades=len(losses),
        win_rate=len(wins) / len(sell_trades) if sell_trades else 0.0,
        avg_return_pct=round(avg_return, 2),
        total_profit=total_profit,
    )
