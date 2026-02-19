"""LLM 사용량 메트릭 — Redis 기반 집계.

서비스별 LLM 호출 횟수, 토큰 사용량을 Redis에 기록.
Dashboard의 /api/llm/stats 에서 조회.

Redis 키: llm:stats:{YYYY-MM-DD}:{service}
    Hash: {calls, tokens_in, tokens_out}
    TTL: 7일
"""

import logging
from datetime import date
from typing import Optional

import redis

logger = logging.getLogger(__name__)

_LLM_STATS_TTL = 86400 * 7  # 7일


def record_llm_usage(
    r: redis.Redis,
    *,
    service: str,
    tokens_in: int,
    tokens_out: int,
    model: str = "",
) -> None:
    """LLM 사용량을 Redis에 기록.

    Args:
        r: Redis 클라이언트
        service: 서비스 식별자 (scout, briefing, macro_council, etc.)
        tokens_in: 입력 토큰 수
        tokens_out: 출력 토큰 수
        model: 사용된 모델명 (로깅용)
    """
    try:
        key = f"llm:stats:{date.today().isoformat()}:{service}"
        pipe = r.pipeline()
        pipe.hincrby(key, "calls", 1)
        pipe.hincrby(key, "tokens_in", tokens_in)
        pipe.hincrby(key, "tokens_out", tokens_out)
        pipe.expire(key, _LLM_STATS_TTL)
        pipe.execute()
    except Exception:
        logger.warning("Failed to record LLM usage for %s", service, exc_info=True)


def get_llm_stats(
    r: redis.Redis,
    target_date: Optional[date] = None,
    service: Optional[str] = None,
) -> dict:
    """LLM 사용량 통계 조회.

    Args:
        target_date: 조회 날짜 (기본: 오늘)
        service: 특정 서비스만 조회 (기본: 전체)
    """
    d = (target_date or date.today()).isoformat()

    if service:
        key = f"llm:stats:{d}:{service}"
        data = r.hgetall(key)
        if not data:
            return {}
        return {
            "calls": int(data.get("calls", 0)),
            "tokens_in": int(data.get("tokens_in", 0)),
            "tokens_out": int(data.get("tokens_out", 0)),
        }

    # 전체 서비스 조회
    services = ["scout", "briefing", "macro_council", "news_analysis", "unknown"]
    result: dict[str, dict] = {}
    for svc in services:
        key = f"llm:stats:{d}:{svc}"
        data = r.hgetall(key)
        if data:
            result[svc] = {
                "calls": int(data.get("calls", 0)),
                "tokens_in": int(data.get("tokens_in", 0)),
                "tokens_out": int(data.get("tokens_out", 0)),
            }
    return result
