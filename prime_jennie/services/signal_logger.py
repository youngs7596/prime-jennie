"""Signal Logger — 매수/매도 시그널 이력 DB 저장 (백테스트용).

Stop/Pause 상태에서 발생한 시그널도 기록하여
나중에 "이 시점에 이 조건이 발동했다"는 데이터를 확보.
"""

import logging

from sqlmodel import Session

from prime_jennie.domain.trading import BuySignal, SellOrder
from prime_jennie.infra.database.engine import get_engine
from prime_jennie.infra.database.models import SignalLogDB

logger = logging.getLogger(__name__)


def log_buy_signal(
    signal: BuySignal,
    *,
    status: str = "published",
    suppressed_reason: str | None = None,
) -> None:
    """매수 시그널 이력 저장."""
    try:
        row = SignalLogDB(
            signal_type="BUY",
            stock_code=signal.stock_code,
            stock_name=signal.stock_name,
            strategy=str(signal.signal_type.value) if signal.signal_type else None,
            price=signal.signal_price,
            hybrid_score=signal.hybrid_score,
            rsi_value=signal.rsi_value,
            volume_ratio=signal.volume_ratio,
            market_regime=str(signal.market_regime.value) if signal.market_regime else None,
            position_multiplier=signal.position_multiplier,
            status=status,
            suppressed_reason=suppressed_reason,
        )
        engine = get_engine()
        with Session(engine) as session:
            session.add(row)
            session.commit()
    except Exception:
        logger.warning("[%s] Failed to log buy signal", signal.stock_code, exc_info=True)


def log_sell_signal(
    order: SellOrder,
    *,
    status: str = "published",
    suppressed_reason: str | None = None,
) -> None:
    """매도 시그널 이력 저장."""
    try:
        row = SignalLogDB(
            signal_type="SELL",
            stock_code=order.stock_code,
            stock_name=order.stock_name,
            strategy=str(order.sell_reason.value) if order.sell_reason else None,
            price=order.current_price,
            quantity=order.quantity,
            market_regime=None,
            profit_pct=order.profit_pct,
            holding_days=order.holding_days,
            status=status,
            suppressed_reason=suppressed_reason,
        )
        engine = get_engine()
        with Session(engine) as session:
            session.add(row)
            session.commit()
    except Exception:
        logger.warning("[%s] Failed to log sell signal", order.stock_code, exc_info=True)
