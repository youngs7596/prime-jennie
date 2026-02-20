"""텔레그램 채널 수집 — hedgecat0301 (키움증권 한지영) 전용.

매크로 Council 파이프라인의 보조 입력으로 사용.
최근 24시간 메시지를 수집하여 브리핑 텍스트로 변환.
"""

import logging
import os
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

# 수집 대상 채널
CHANNEL_USERNAME = "hedgecat0301"
CHANNEL_NAME = "키움증권 전략/시황 한지영"

# 매크로 키워드 (감지용)
MACRO_KEYWORDS = [
    "금리",
    "환율",
    "FOMC",
    "물가",
    "CPI",
    "GDP",
    "경기",
    "침체",
    "인플레이션",
    "관세",
    "트럼프",
    "달러",
    "연준",
    "BOK",
    "기준금리",
    "VIX",
    "원/달러",
    "국채",
    "고용",
    "PMI",
    "ISM",
]

# 세션 파일 탐색 경로
SESSION_SEARCH_PATHS = [
    "/app/.telegram_sessions",
    os.path.join(os.path.dirname(__file__), "../../../.telegram_sessions"),
]


def _find_session_path() -> str:
    """Telethon 세션 파일 경로를 탐색."""
    for candidate in SESSION_SEARCH_PATHS:
        resolved = os.path.realpath(candidate)
        if os.path.isdir(resolved):
            return os.path.join(resolved, "telegram_collector")
    # fallback
    return "/app/.telegram_sessions/telegram_collector"


def _detect_macro_keywords(text: str) -> list[str]:
    """텍스트에서 매크로 키워드 감지."""
    return [kw for kw in MACRO_KEYWORDS if kw in text]


def _format_briefing(messages: list[dict]) -> str:
    """수집된 메시지를 브리핑 텍스트로 변환.

    Args:
        messages: [{"text": str, "date": datetime, "macro_keywords": list}]

    Returns:
        포맷된 브리핑 텍스트
    """
    if not messages:
        return ""

    lines = [f"=== 텔레그램 브리핑 ({CHANNEL_NAME}) ==="]
    for i, msg in enumerate(messages, 1):
        kst = msg["date"] + timedelta(hours=9)
        time_str = kst.strftime("%m/%d %H:%M")
        keywords = msg.get("macro_keywords", [])
        kw_tag = f" [{', '.join(keywords)}]" if keywords else ""

        lines.append(f"\n[{i}] {time_str}{kw_tag}")
        # 메시지 본문 (최대 500자)
        text = msg["text"].strip()
        if len(text) > 500:
            text = text[:500] + "..."
        lines.append(text)

    return "\n".join(lines)


async def collect_hedgecat_briefing(hours: int = 24, max_messages: int = 30) -> str:
    """hedgecat0301 채널에서 최근 메시지를 수집하여 브리핑 텍스트로 반환.

    Args:
        hours: 몇 시간 이내 메시지만 수집 (default: 24)
        max_messages: 최대 수집 메시지 수 (default: 30)

    Returns:
        포맷된 브리핑 텍스트. 실패 시 빈 문자열.
    """
    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")

    if not api_id or not api_hash:
        logger.warning("TELEGRAM_API_ID/HASH not set, skipping telegram collection")
        return ""

    try:
        from telethon import TelegramClient
        from telethon.tl.functions.messages import GetHistoryRequest
    except ImportError:
        logger.warning("telethon not installed, skipping telegram collection")
        return ""

    session_path = _find_session_path()
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    collected = []

    try:
        async with TelegramClient(session_path, int(api_id), api_hash) as client:
            entity = await client.get_entity(CHANNEL_USERNAME)
            history = await client(
                GetHistoryRequest(
                    peer=entity,
                    limit=max_messages,
                    offset_id=0,
                    offset_date=None,
                    add_offset=0,
                    max_id=0,
                    min_id=0,
                    hash=0,
                )
            )

            for msg in history.messages:
                if not msg.message:
                    continue

                msg_time = msg.date.replace(tzinfo=UTC)
                if msg_time < cutoff:
                    continue

                text = msg.message.strip()
                # 너무 짧은 메시지 스킵
                if len(text) < 20:
                    continue

                collected.append(
                    {
                        "text": text,
                        "date": msg_time,
                        "macro_keywords": _detect_macro_keywords(text),
                    }
                )

        logger.info("Telegram: collected %d messages from @%s", len(collected), CHANNEL_USERNAME)

    except Exception:
        logger.exception("Telegram collection failed for @%s", CHANNEL_USERNAME)
        return ""

    # 시간순 정렬 (오래된 것 먼저)
    collected.sort(key=lambda m: m["date"])
    return _format_briefing(collected)
