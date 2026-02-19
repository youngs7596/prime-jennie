"""Trades API — 거래 기록 조회."""

from fastapi import APIRouter, Depends
from sqlmodel import Session

from prime_jennie.infra.database.repositories import PortfolioRepository
from prime_jennie.services.deps import get_db_session

router = APIRouter(prefix="/trades", tags=["trades"])


@router.get("/recent")
def get_recent_trades(
    days: int = 7,
    session: Session = Depends(get_db_session),
) -> list[dict]:
    """최근 거래 기록."""
    trades = PortfolioRepository.get_recent_trades(session, days=days)
    return [
        {
            "id": t.id,
            "stock_code": t.stock_code,
            "stock_name": t.stock_name,
            "trade_type": t.trade_type,
            "quantity": t.quantity,
            "price": t.price,
            "total_amount": t.total_amount,
            "reason": t.reason,
            "strategy_signal": t.strategy_signal,
            "market_regime": t.market_regime,
            "llm_score": t.llm_score,
            "hybrid_score": t.hybrid_score,
            "trade_tier": t.trade_tier,
            "profit_pct": t.profit_pct,
            "profit_amount": t.profit_amount,
            "holding_days": t.holding_days,
            "timestamp": t.trade_timestamp.isoformat() if t.trade_timestamp else None,
        }
        for t in trades
    ]
