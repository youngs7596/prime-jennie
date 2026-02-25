"""Buy Executor 서비스 — 매수 시그널 소비 및 주문 실행.

Redis Stream에서 BuySignal을 소비하여 KIS Gateway로 주문.

Data Flow:
  Redis stream:buy-signals → Executor → KIS Gateway → DB trade_logs
"""

import logging
import threading
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from sqlmodel import Session

from prime_jennie.domain import BuySignal, TradeNotification
from prime_jennie.domain.config import get_config
from prime_jennie.infra.database.engine import get_engine
from prime_jennie.infra.database.models import PositionDB, TradeLogDB
from prime_jennie.infra.database.repositories import PortfolioRepository
from prime_jennie.infra.kis.client import KISClient
from prime_jennie.infra.redis.client import get_redis
from prime_jennie.infra.redis.streams import TypedStreamConsumer, TypedStreamPublisher
from prime_jennie.services.base import create_app

from .executor import BuyExecutor, ExecutionResult
from .portfolio_guard import PortfolioGuard

logger = logging.getLogger(__name__)

# Stream 키
STREAM_BUY_SIGNALS = "stream:buy-signals"
STREAM_TRADE_NOTIFICATIONS = "stream:trade-notifications"
GROUP_BUY_EXECUTOR = "group_buy_executor"

# 모듈 레벨 싱글턴
_executor: BuyExecutor | None = None
_consumer: TypedStreamConsumer | None = None
_consumer_thread: threading.Thread | None = None
_notifier: TypedStreamPublisher | None = None


def _handle_signal(signal: BuySignal) -> None:
    """Redis Stream 메시지 핸들러."""
    if _executor is None:
        logger.error("Executor not initialized")
        return

    try:
        result = _executor.process_signal(signal)

        if result.status == "success":
            _persist_buy(signal, result)
            _notify_buy(signal, result)

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


def _persist_buy(signal: BuySignal, result: ExecutionResult) -> None:
    """매수 체결 → trade_logs + positions 저장."""
    try:
        engine = get_engine()
        with Session(engine) as session:
            trade = TradeLogDB(
                stock_code=result.stock_code,
                stock_name=result.stock_name,
                trade_type="BUY",
                quantity=result.quantity,
                price=result.price,
                total_amount=result.quantity * result.price,
                reason=signal.signal_type,
                strategy_signal=signal.signal_type,
                market_regime=str(signal.market_regime),
                llm_score=signal.llm_score,
                hybrid_score=signal.hybrid_score,
                trade_tier=str(signal.trade_tier),
            )
            PortfolioRepository.save_trade_log(session, trade)

            config = get_config()
            stop_loss_price = int(result.price * (1 - config.sell.stop_loss_pct / 100))
            pos = PositionDB(
                stock_code=result.stock_code,
                stock_name=result.stock_name,
                quantity=result.quantity,
                average_buy_price=result.price,
                total_buy_amount=result.quantity * result.price,
                sector_group=str(signal.sector_group) if signal.sector_group else None,
                high_watermark=result.price,
                stop_loss_price=stop_loss_price,
            )
            PortfolioRepository.upsert_position(session, pos)
    except Exception:
        logger.exception("[%s] Failed to persist buy trade to DB", result.stock_code)


def _notify_buy(signal: BuySignal, result: ExecutionResult) -> None:
    """매수 체결 알림 발행 (fire-and-forget)."""
    if _notifier is None:
        return
    try:
        notification = TradeNotification(
            trade_type="BUY",
            stock_code=result.stock_code,
            stock_name=result.stock_name,
            quantity=result.quantity,
            price=result.price,
            total_amount=result.quantity * result.price,
            signal_type=str(signal.signal_type),
            trade_tier=str(signal.trade_tier),
            hybrid_score=signal.hybrid_score,
            timestamp=datetime.now(UTC),
        )
        _notifier.publish(notification)
    except Exception:
        logger.exception("[%s] Failed to publish buy notification", result.stock_code)


@asynccontextmanager
async def lifespan(app):
    """서비스 시작/종료 관리."""
    global _executor, _consumer, _consumer_thread, _notifier

    config = get_config()
    redis_client = get_redis()
    kis_client = KISClient()
    guard = PortfolioGuard(redis_client)

    _executor = BuyExecutor(kis_client, redis_client, guard)
    _notifier = TypedStreamPublisher(redis_client, STREAM_TRADE_NOTIFICATIONS, TradeNotification)

    # Stream consumer 시작 (daemon thread)
    _consumer = TypedStreamConsumer(
        client=redis_client,
        stream=STREAM_BUY_SIGNALS,
        group=GROUP_BUY_EXECUTOR,
        consumer=f"executor-{config.env}",
        model_class=BuySignal,
        handler=_handle_signal,
    )
    _consumer_thread = threading.Thread(target=_consumer.run, name="buy-signal-consumer", daemon=True)
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
