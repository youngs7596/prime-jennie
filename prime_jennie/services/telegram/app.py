"""Telegram Command Handler — FastAPI 앱 + 폴링 루프.

텔레그램 Bot API 폴링 → 명령 파싱 → 핸들러 실행 → 응답 발송.
"""

import logging
import threading
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI

from prime_jennie.domain.config import get_config
from prime_jennie.infra.database.engine import get_engine
from prime_jennie.infra.redis.client import get_redis
from prime_jennie.infra.kis.client import KISClient
from prime_jennie.services.base import create_app

from .bot import TelegramBot
from .handler import CommandHandler

logger = logging.getLogger(__name__)

_bot: Optional[TelegramBot] = None
_handler: Optional[CommandHandler] = None
_polling_thread: Optional[threading.Thread] = None
_polling_active = threading.Event()


def _get_bot() -> TelegramBot:
    global _bot
    if _bot is None:
        config = get_config()
        _bot = TelegramBot(
            token=config.telegram.bot_token,
            allowed_chat_ids=os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", ""),
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


import os

app = create_app("command-handler", version="1.0.0", dependencies=["redis", "db"])


@app.post("/start")
def start_polling():
    """폴링 시작."""
    global _polling_thread
    if _polling_active.is_set():
        return {"status": "already_running"}

    bot = _get_bot()
    handler = _get_handler()

    _polling_active.set()
    _polling_thread = threading.Thread(
        target=_poll_loop, args=(bot, handler), daemon=True
    )
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
