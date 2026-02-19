"""Sell Executor FastAPI App — Redis Stream 소비 + HTTP API."""

import logging
import threading
from contextlib import asynccontextmanager

import redis

from prime_jennie.domain.config import get_config
from prime_jennie.domain.trading import SellOrder
from prime_jennie.infra.kis.client import KISClient
from prime_jennie.infra.redis.streams import TypedStreamConsumer
from prime_jennie.services.base import create_app

from .executor import SellExecutor

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


@app.get("/status")
def status():
    return {"service": "sell-executor", "status": "running"}
