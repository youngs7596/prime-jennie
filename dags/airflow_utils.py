"""Airflow 공용 유틸리티 — 텔레그램 알림 + 공통 설정."""

import logging
import os
from datetime import timedelta

import requests

logger = logging.getLogger(__name__)

# Airflow 공통 default_args
DEFAULT_ARGS = {
    "owner": "jennie",
    "depends_on_past": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
    "on_failure_callback": None,  # 아래에서 set
}


def get_telegram_config() -> tuple[str | None, str | None]:
    """Telegram bot token + chat_ids 로드 (환경변수 전용)."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_ids = os.getenv("TELEGRAM_CHAT_IDS", os.getenv("TELEGRAM_CHAT_ID", ""))
    return token, chat_ids or None


def send_telegram_alert(context: dict) -> None:
    """DAG 실패 시 텔레그램 알림 전송."""
    token, chat_id = get_telegram_config()
    if not token or not chat_id:
        logger.warning("Telegram config not found, skipping alert")
        return

    dag_id = context.get("dag", {}).dag_id if context.get("dag") else "unknown"
    task_id = context.get("task_instance", {}).task_id if context.get("task_instance") else "unknown"
    execution_date = str(context.get("execution_date", ""))
    exception = str(context.get("exception", ""))[:500]

    message = (
        f"[Airflow Alert]\n"
        f"DAG: {dag_id}\n"
        f"Task: {task_id}\n"
        f"Date: {execution_date}\n"
        f"Error: {exception}"
    )

    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.post(url, json={"chat_id": chat_id, "text": message}, timeout=10)
    except Exception as e:
        logger.error("Failed to send Telegram alert: %s", e)


def get_default_args(**overrides) -> dict:
    """공통 default_args + 오버라이드."""
    args = {**DEFAULT_ARGS, "on_failure_callback": send_telegram_alert}
    args.update(overrides)
    return args


def service_url(service: str, port: int, path: str) -> str:
    """서비스 HTTP URL 생성."""
    host = os.getenv(f"{service.upper().replace('-', '_')}_HOST", service)
    return f"http://{host}:{port}{path}"
