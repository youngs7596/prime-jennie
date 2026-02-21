"""Telegram Command Handler — FastAPI 앱 + 폴링 루프 + 체결 알림 소비.

텔레그램 Bot API 폴링 → 명령 파싱 → 핸들러 실행 → 응답 발송.
Redis Stream 체결 알림 → 텔레그램 메시지 발송.
"""

import logging
import threading
import time
from contextlib import asynccontextmanager

from prime_jennie.domain.config import get_config
from prime_jennie.domain.notification import TradeNotification
from prime_jennie.infra.database.engine import get_engine
from prime_jennie.infra.kis.client import KISClient
from prime_jennie.infra.redis.client import get_redis
from prime_jennie.infra.redis.streams import TypedStreamConsumer
from prime_jennie.services.base import create_app

from .bot import TelegramBot
from .handler import CommandHandler

logger = logging.getLogger(__name__)

STREAM_TRADE_NOTIFICATIONS = "stream:trade-notifications"
GROUP_TELEGRAM = "group_telegram"
KEY_MUTE_UNTIL = "notification:mute_until"

_bot: TelegramBot | None = None
_handler: CommandHandler | None = None
_polling_thread: threading.Thread | None = None
_polling_active = threading.Event()
_notification_consumer: TypedStreamConsumer | None = None
_notification_thread: threading.Thread | None = None


def _get_bot() -> TelegramBot:
    global _bot
    if _bot is None:
        config = get_config()
        _bot = TelegramBot(
            token=config.telegram.bot_token,
            allowed_chat_ids=config.telegram.chat_ids,
        )
    return _bot


def _get_handler() -> CommandHandler:
    global _handler
    if _handler is None:
        config = get_config()
        redis_client = get_redis()
        kis_client = KISClient(base_url=config.kis.gateway_url)

        from sqlmodel import Session

        engine = get_engine()

        def session_factory():
            return Session(engine)

        _handler = CommandHandler(redis_client, kis_client, session_factory)
    return _handler


def _is_muted() -> bool:
    """음소거 상태 확인."""
    try:
        redis_client = get_redis()
        mute_until = redis_client.get(KEY_MUTE_UNTIL)
        if mute_until:
            return int(mute_until) > int(time.time())
    except Exception:
        pass
    return False


def _format_trade_message(n: TradeNotification) -> str:
    """체결 알림 메시지 포매팅 (Markdown)."""
    if n.trade_type == "BUY":
        lines = [
            "*[매수 체결]*",
            f"{n.stock_name} ({n.stock_code})",
            f"수량: {n.quantity}주 | 가격: {n.price:,}원",
            f"금액: {n.total_amount:,}원",
        ]
        parts = []
        if n.signal_type:
            parts.append(n.signal_type)
        if n.trade_tier:
            parts.append(n.trade_tier)
        if parts:
            lines.append(f"전략: {' / '.join(parts)}")
        if n.hybrid_score is not None:
            lines.append(f"점수: {n.hybrid_score:.1f}")
    else:
        lines = [
            "*[매도 체결]*",
            f"{n.stock_name} ({n.stock_code})",
            f"수량: {n.quantity}주 | 가격: {n.price:,}원",
            f"금액: {n.total_amount:,}원",
        ]
        if n.profit_pct is not None:
            lines.append(f"수익률: {n.profit_pct:+.2f}%")
        if n.sell_reason:
            lines.append(f"사유: {n.sell_reason}")
        if n.holding_days is not None:
            lines.append(f"보유: {n.holding_days}일")

    return "\n".join(lines)


def _handle_trade_notification(notification: TradeNotification) -> None:
    """체결 알림 처리 — 음소거 체크 후 모든 chat_id로 발송."""
    if _is_muted():
        logger.debug("Trade notification muted, skipping")
        return

    bot = _get_bot()
    message = _format_trade_message(notification)

    config = get_config()
    chat_ids = [cid.strip() for cid in config.telegram.chat_ids.split(",") if cid.strip()]

    for chat_id in chat_ids:
        try:
            bot.send_message(chat_id, message)
        except Exception:
            logger.exception("Failed to send trade notification to %s", chat_id)


def _poll_loop(bot: TelegramBot, handler: CommandHandler) -> None:
    """폴링 루프 (백그라운드 스레드)."""
    logger.info("Telegram polling started")
    while _polling_active.is_set():
        try:
            commands = bot.get_pending_commands()
            for cmd in commands:
                response = handler.process_command(
                    cmd["command"],
                    cmd["args"],
                    chat_id=cmd["chat_id"],
                    username=cmd["username"],
                )
                bot.send_message(cmd["chat_id"], response)
        except Exception:
            logger.exception("Polling loop error")
            time.sleep(5)
    logger.info("Telegram polling stopped")


@asynccontextmanager
async def _lifespan(app):
    """서비스 시작/종료 — 체결 알림 consumer 자동 시작."""
    global _notification_consumer, _notification_thread

    redis_client = get_redis()
    config = get_config()

    _notification_consumer = TypedStreamConsumer(
        client=redis_client,
        stream=STREAM_TRADE_NOTIFICATIONS,
        group=GROUP_TELEGRAM,
        consumer=f"telegram-{config.env}",
        model_class=TradeNotification,
        handler=_handle_trade_notification,
    )
    _notification_thread = threading.Thread(
        target=_notification_consumer.run,
        name="trade-notification-consumer",
        daemon=True,
    )
    _notification_thread.start()
    logger.info("Trade notification consumer started")

    yield

    if _notification_consumer:
        _notification_consumer.stop()
    if _notification_thread and _notification_thread.is_alive():
        _notification_thread.join(timeout=10)
    logger.info("Trade notification consumer stopped")


app = create_app("command-handler", version="1.0.0", lifespan=_lifespan, dependencies=["redis", "db"])


@app.post("/start")
def start_polling():
    """폴링 시작."""
    global _polling_thread
    if _polling_active.is_set():
        return {"status": "already_running"}

    bot = _get_bot()
    handler = _get_handler()

    _polling_active.set()
    _polling_thread = threading.Thread(target=_poll_loop, args=(bot, handler), daemon=True)
    _polling_thread.start()
    return {"status": "started"}


@app.post("/stop")
def stop_polling():
    """폴링 중지."""
    _polling_active.clear()
    return {"status": "stopped"}


@app.post("/poll")
def manual_poll():
    """수동 1회 폴링."""
    bot = _get_bot()
    handler = _get_handler()

    commands = bot.get_pending_commands()
    results = []
    for cmd in commands:
        response = handler.process_command(
            cmd["command"],
            cmd["args"],
            chat_id=cmd["chat_id"],
            username=cmd["username"],
        )
        bot.send_message(cmd["chat_id"], response)
        results.append({"command": cmd["command"], "response": response[:100]})

    return {"processed": len(results), "commands": results}
