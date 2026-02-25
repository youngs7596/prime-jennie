"""Portfolio API — 포트폴리오 상태, 보유 종목, 자산 히스토리."""

import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session

from prime_jennie.domain.portfolio import DailySnapshot, PortfolioState, Position
from prime_jennie.infra.database.repositories import (
    AssetSnapshotRepository,
    PortfolioRepository,
)
from prime_jennie.infra.kis.client import KISClient
from prime_jennie.infra.redis.client import get_redis
from prime_jennie.services.deps import get_db_session

logger = logging.getLogger(__name__)

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
    """포트폴리오 전체 요약 (KIS 실시간 잔고 우선, 실패 시 DB 스냅샷 fallback)."""
    # KIS 실시간 잔고 조회 시도
    try:
        kis = KISClient()
        balance = kis.get_balance()
        kis.close()

        kis_positions = {p["stock_code"]: p for p in balance.get("positions", [])}
        positions_db = PortfolioRepository.get_positions(session)
        db_map = {p.stock_code: p for p in positions_db}

        # KIS 실시간 포지션 기준, DB 메타데이터 병합
        positions = []
        for code, kp in kis_positions.items():
            db_pos = db_map.get(code)
            positions.append(
                Position(
                    stock_code=kp["stock_code"],
                    stock_name=kp["stock_name"],
                    quantity=kp["quantity"],
                    average_buy_price=kp["average_buy_price"],
                    total_buy_amount=kp["total_buy_amount"],
                    current_price=kp.get("current_price"),
                    current_value=kp.get("current_value"),
                    profit_pct=kp.get("profit_pct"),
                    sector_group=db_pos.sector_group if db_pos else None,
                    high_watermark=db_pos.high_watermark if db_pos else None,
                    stop_loss_price=db_pos.stop_loss_price if db_pos else None,
                )
            )

        cash = int(balance.get("cash_balance", 0))
        total = int(balance.get("total_asset", 0))
        stock_eval = int(balance.get("stock_eval_amount", 0))

        return PortfolioState(
            positions=positions,
            cash_balance=cash,
            total_asset=total,
            stock_eval_amount=stock_eval,
            position_count=len(positions),
            timestamp=datetime.now(UTC),
        )
    except Exception:
        logger.warning("KIS 실시간 잔고 조회 실패, DB 스냅샷 fallback", exc_info=True)

    # Fallback: DB 스냅샷
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
        timestamp=datetime.now(UTC),
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


@router.get("/live")
def get_live_positions(session: Session = Depends(get_db_session)) -> dict:
    """Monitor가 캐싱한 실시간 포지션 스냅샷. Redis 비면 KIS/DB fallback."""
    # 1차: Redis (Monitor 30초 갱신)
    try:
        r = get_redis()
        raw = r.get("monitoring:live_positions")
        if raw:
            return json.loads(raw)
    except Exception:
        logger.debug("Redis live_positions read failed", exc_info=True)

    # 2차: KIS 실시간 + DB 메타데이터 (fallback)
    try:
        kis = KISClient()
        balance = kis.get_balance()
        kis.close()

        kis_positions = {p["stock_code"]: p for p in balance.get("positions", [])}
        db_map = {p.stock_code: p for p in PortfolioRepository.get_positions(session)}

        positions = []
        for code, kp in kis_positions.items():
            db_pos = db_map.get(code)
            positions.append(
                {
                    "stock_code": kp["stock_code"],
                    "stock_name": kp["stock_name"],
                    "quantity": kp["quantity"],
                    "average_buy_price": kp["average_buy_price"],
                    "total_buy_amount": kp["total_buy_amount"],
                    "current_price": kp.get("current_price"),
                    "current_value": kp.get("current_value"),
                    "profit_pct": kp.get("profit_pct"),
                    "sector_group": db_pos.sector_group if db_pos else None,
                    "high_watermark": db_pos.high_watermark if db_pos else None,
                    "stop_loss_price": db_pos.stop_loss_price if db_pos else None,
                }
            )
        return {
            "positions": positions,
            "updated_at": datetime.now(UTC).isoformat(),
        }
    except Exception:
        logger.debug("KIS fallback for live positions failed", exc_info=True)

    # 3차: DB only (가격 없이라도 보여줌)
    positions_db = PortfolioRepository.get_positions(session)
    positions = [
        {
            "stock_code": p.stock_code,
            "stock_name": p.stock_name,
            "quantity": p.quantity,
            "average_buy_price": p.average_buy_price,
            "total_buy_amount": p.total_buy_amount,
            "current_price": None,
            "current_value": None,
            "profit_pct": None,
            "sector_group": p.sector_group,
            "high_watermark": p.high_watermark,
            "stop_loss_price": p.stop_loss_price,
        }
        for p in positions_db
    ]
    return {"positions": positions, "updated_at": None}


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
    avg_return = sum(t.profit_pct or 0 for t in sell_trades) / len(sell_trades) if sell_trades else 0.0

    return PerformanceSummary(
        total_trades=len(sell_trades),
        win_trades=len(wins),
        loss_trades=len(losses),
        win_rate=len(wins) / len(sell_trades) if sell_trades else 0.0,
        avg_return_pct=round(avg_return, 2),
        total_profit=total_profit,
    )
