"""Sell Executor FastAPI App — Redis Stream 소비 + HTTP API."""

import logging
import threading
from contextlib import asynccontextmanager

import redis
from sqlmodel import Session

from prime_jennie.domain.config import get_config
from prime_jennie.domain.trading import SellOrder
from prime_jennie.infra.database.engine import get_engine
from prime_jennie.infra.database.models import TradeLogDB
from prime_jennie.infra.database.repositories import PortfolioRepository
from prime_jennie.infra.kis.client import KISClient
from prime_jennie.infra.redis.streams import TypedStreamConsumer
from prime_jennie.services.base import create_app

from .executor import SellExecutor, SellResult

logger = logging.getLogger(__name__)

SELL_SIGNAL_STREAM = "stream:sell-orders"
SELL_GROUP = "group_sell_executor"


@asynccontextmanager
async def lifespan(app):
    """Startup: Redis Stream consumer 시작."""
    config = get_config()
    r = redis.Redis.from_url(config.redis.url, decode_responses=True)
    kis = KISClient()
    executor = SellExecutor(kis, r)
    app.state.executor = executor

    def handler(order: SellOrder):
        result = executor.process_signal(order)

        if result.status == "success":
            _persist_sell(order, result)

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
            trade = TradeLogDB(
                stock_code=result.stock_code,
                stock_name=result.stock_name,
                trade_type="SELL",
                quantity=result.quantity,
                price=result.price,
                total_amount=result.quantity * result.price,
                reason=str(order.sell_reason),
                strategy_signal=str(order.sell_reason),
                profit_pct=result.profit_pct,
                profit_amount=int(result.profit_pct / 100 * order.buy_price * result.quantity)
                if order.buy_price
                else None,
                holding_days=order.holding_days,
            )
            PortfolioRepository.save_trade_log(session, trade)

            PortfolioRepository.reduce_position(session, result.stock_code, result.quantity)
    except Exception:
        logger.exception("[%s] Failed to persist sell trade to DB", result.stock_code)


@app.get("/status")
def status():
    return {"service": "sell-executor", "status": "running"}
