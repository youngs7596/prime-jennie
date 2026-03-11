"""LLM 사용량 메트릭 — Redis 기반 집계.

서비스별 LLM 호출 횟수, 토큰 사용량을 Redis에 기록.
Dashboard의 /api/llm/stats 에서 조회.

Redis 키: llm:stats:{YYYY-MM-DD}:{service}
    Hash: {calls, tokens_in, tokens_out}
    TTL: 35일 (월별 집계용)
"""

import logging
from calendar import monthrange
from datetime import date

import redis

logger = logging.getLogger(__name__)

_LLM_STATS_TTL = 86400 * 35  # 35일 (월별 집계 보장)


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
    target_date: date | None = None,
    service: str | None = None,
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
    services = ["scout", "briefing", "macro_council", "news_analysis", "wsj_summary", "unknown"]
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


def get_llm_monthly_stats(
    r: redis.Redis,
    target_month: str | None = None,
) -> dict:
    """월별 LLM 사용량 집계.

    Args:
        target_month: 'YYYY-MM' 형식 (기본: 이번 달)

    Returns:
        서비스별 월간 합산 {service: {calls, tokens_in, tokens_out}}
    """
    today = date.today()
    if target_month:
        year, month = map(int, target_month.split("-"))
    else:
        year, month = today.year, today.month

    days_in_month = monthrange(year, month)[1]
    services = ["scout", "briefing", "macro_council", "news_analysis", "wsj_summary", "unknown"]

    # Pipeline으로 한 번에 조회
    pipe = r.pipeline()
    keys = []
    for day in range(1, days_in_month + 1):
        d = date(year, month, day)
        if d > today:
            break
        for svc in services:
            key = f"llm:stats:{d.isoformat()}:{svc}"
            pipe.hgetall(key)
            keys.append(svc)

    results = pipe.execute()

    # 집계
    agg: dict[str, dict] = {}
    for svc, data in zip(keys, results, strict=True):
        if not data:
            continue
        if svc not in agg:
            agg[svc] = {"calls": 0, "tokens_in": 0, "tokens_out": 0}
        agg[svc]["calls"] += int(data.get("calls", 0))
        agg[svc]["tokens_in"] += int(data.get("tokens_in", 0))
        agg[svc]["tokens_out"] += int(data.get("tokens_out", 0))

    return agg
