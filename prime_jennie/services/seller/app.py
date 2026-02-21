"""Sell Executor FastAPI App — Redis Stream 소비 + HTTP API."""

import logging
import threading
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import redis
from sqlmodel import Session

from prime_jennie.domain.config import get_config
from prime_jennie.domain.notification import TradeNotification
from prime_jennie.domain.trading import SellOrder
from prime_jennie.infra.database.engine import get_engine
from prime_jennie.infra.database.models import TradeLogDB
from prime_jennie.infra.database.repositories import PortfolioRepository
from prime_jennie.infra.kis.client import KISClient
from prime_jennie.infra.redis.streams import TypedStreamConsumer, TypedStreamPublisher
from prime_jennie.services.base import create_app

from .executor import SellExecutor, SellResult

logger = logging.getLogger(__name__)

SELL_SIGNAL_STREAM = "stream:sell-orders"
SELL_GROUP = "group_sell_executor"
STREAM_TRADE_NOTIFICATIONS = "stream:trade-notifications"


@asynccontextmanager
async def lifespan(app):
    """Startup: Redis Stream consumer 시작."""
    config = get_config()
    r = redis.Redis.from_url(config.redis.url, decode_responses=True)
    kis = KISClient()
    executor = SellExecutor(kis, r)
    app.state.executor = executor
    notifier = TypedStreamPublisher(r, STREAM_TRADE_NOTIFICATIONS, TradeNotification)

    def handler(order: SellOrder):
        result = executor.process_signal(order)

        if result.status == "success":
            _persist_sell(order, result)
            _notify_sell(order, result, notifier)

        logger.info(
            "[%s] Sell result: %s (%s)",
            order.stock_code,
            result.status,
            result.reason,
        )

    consumer = TypedStreamConsumer(
        client=r,
        stream=SELL_SIGNAL_STREAM,
        group=SELL_GROUP,
        consumer=f"sell-executor-{threading.get_ident()}",
        model_class=SellOrder,
        handler=handler,
    )

    thread = threading.Thread(target=consumer.run, daemon=True)
    thread.start()
    app.state.consumer = consumer

    yield

    consumer.stop()
    kis.close()


app = create_app("sell-executor", version="1.0.0", lifespan=lifespan)


def _persist_sell(order: SellOrder, result: SellResult) -> None:
    """매도 체결 → trade_logs + positions 업데이트."""
    try:
        engine = get_engine()
        with Session(engine) as session:
            # 매수 전략 조회 (해당 종목의 가장 최근 BUY 로그)
            buy_signal = _lookup_buy_strategy(session, result.stock_code)

            # profit_pct: executor 계산값 우선, 없으면 order에서
            profit_pct = result.profit_pct
            if profit_pct is None or (profit_pct == 0.0 and order.profit_pct is not None):
                profit_pct = order.profit_pct

            # profit_amount 계산
            profit_amount = None
            buy_price = order.buy_price or 0
            if profit_pct is not None and buy_price > 0:
                profit_amount = int((result.price - buy_price) * result.quantity)

            trade = TradeLogDB(
                stock_code=result.stock_code,
                stock_name=result.stock_name,
                trade_type="SELL",
                quantity=result.quantity,
                price=result.price,
                total_amount=result.quantity * result.price,
                reason=str(order.sell_reason),
                strategy_signal=buy_signal,
                profit_pct=profit_pct,
                profit_amount=profit_amount,
                holding_days=order.holding_days,
            )
            PortfolioRepository.save_trade_log(session, trade)

            PortfolioRepository.reduce_position(session, result.stock_code, result.quantity)
    except Exception:
        logger.exception("[%s] Failed to persist sell trade to DB", result.stock_code)


def _lookup_buy_strategy(session: Session, stock_code: str) -> str | None:
    """해당 종목의 가장 최근 BUY 로그에서 strategy_signal 조회."""
    from sqlmodel import text

    row = session.exec(
        text(
            "SELECT strategy_signal FROM trade_logs "
            "WHERE trade_type = 'BUY' AND stock_code = :code "
            "ORDER BY trade_timestamp DESC LIMIT 1"
        ).bindparams(code=stock_code)
    ).first()
    return row[0] if row else None


def _notify_sell(order: SellOrder, result: SellResult, notifier: TypedStreamPublisher) -> None:
    """매도 체결 알림 발행 (fire-and-forget)."""
    try:
        notification = TradeNotification(
            trade_type="SELL",
            stock_code=result.stock_code,
            stock_name=result.stock_name,
            quantity=result.quantity,
            price=result.price,
            total_amount=result.quantity * result.price,
            sell_reason=str(order.sell_reason),
            profit_pct=result.profit_pct,
            holding_days=order.holding_days,
            timestamp=datetime.now(UTC),
        )
        notifier.publish(notification)
    except Exception:
        logger.exception("[%s] Failed to publish sell notification", result.stock_code)


@app.get("/status")
def status():
    return {"service": "sell-executor", "status": "running"}
