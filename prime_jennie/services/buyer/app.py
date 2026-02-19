"""Buy Executor 서비스 — 매수 시그널 소비 및 주문 실행.

Redis Stream에서 BuySignal을 소비하여 KIS Gateway로 주문.

Data Flow:
  Redis stream:buy-signals → Executor → KIS Gateway → DB trade_logs
"""

import logging
import threading
from contextlib import asynccontextmanager
from typing import Optional

import redis

from prime_jennie.domain import BuySignal
from prime_jennie.domain.config import get_config
from prime_jennie.infra.kis.client import KISClient
from prime_jennie.infra.redis.client import get_redis
from prime_jennie.infra.redis.streams import TypedStreamConsumer
from prime_jennie.services.base import create_app

from .executor import BuyExecutor
from .portfolio_guard import PortfolioGuard

logger = logging.getLogger(__name__)

# Stream 키
STREAM_BUY_SIGNALS = "stream:buy-signals"
GROUP_BUY_EXECUTOR = "group_buy_executor"

# 모듈 레벨 싱글턴
_executor: Optional[BuyExecutor] = None
_consumer: Optional[TypedStreamConsumer] = None
_consumer_thread: Optional[threading.Thread] = None


def _handle_signal(signal: BuySignal) -> None:
    """Redis Stream 메시지 핸들러."""
    if _executor is None:
        logger.error("Executor not initialized")
        return

    try:
        result = _executor.process_signal(signal)
        logger.info(
            "[%s] %s: %s (qty=%d, price=%d)",
            signal.stock_code,
            result.status,
            result.reason or "OK",
            result.quantity,
            result.price,
        )
    except Exception:
        logger.exception("[%s] Signal processing failed", signal.stock_code)


@asynccontextmanager
async def lifespan(app):
    """서비스 시작/종료 관리."""
    global _executor, _consumer, _consumer_thread

    config = get_config()
    redis_client = get_redis()
    kis_client = KISClient()
    guard = PortfolioGuard(redis_client)

    _executor = BuyExecutor(kis_client, redis_client, guard)

    # Stream consumer 시작 (daemon thread)
    _consumer = TypedStreamConsumer(
        client=redis_client,
        stream=STREAM_BUY_SIGNALS,
        group=GROUP_BUY_EXECUTOR,
        consumer=f"executor-{config.env}",
        model_class=BuySignal,
        handler=_handle_signal,
    )
    _consumer_thread = threading.Thread(
        target=_consumer.run, name="buy-signal-consumer", daemon=True
    )
    _consumer_thread.start()
    logger.info("Buy executor started, consuming from %s", STREAM_BUY_SIGNALS)

    yield

    # Shutdown
    if _consumer:
        _consumer.stop()
    if _consumer_thread and _consumer_thread.is_alive():
        _consumer_thread.join(timeout=10)
    kis_client.close()
    logger.info("Buy executor shutdown complete")


app = create_app(
    "buy-executor",
    version="1.0.0",
    lifespan=lifespan,
    dependencies=["redis", "kis"],
)


@app.get("/status")
async def status():
    """Executor 상태."""
    return {
        "executor_ready": _executor is not None,
        "consumer_alive": _consumer_thread.is_alive() if _consumer_thread else False,
        "emergency_stop": False,
    }
