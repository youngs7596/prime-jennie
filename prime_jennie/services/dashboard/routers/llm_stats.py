"""LLM Stats API — LLM 사용량 통계."""

from datetime import date

import redis
from fastapi import APIRouter, Depends

from prime_jennie.services.deps import get_redis_client

router = APIRouter(prefix="/llm", tags=["llm"])

# 추적 대상 서비스 목록
_SERVICES = ["scout", "briefing", "macro_council", "news_analysis", "unknown"]


@router.get("/stats/{target_date}")
def get_stats(
    target_date: str,
    r: redis.Redis = Depends(get_redis_client),
) -> dict:
    """특정 날짜의 LLM 사용량 통계.

    Redis 키: llm:stats:{date}:{service} → {calls, tokens_in, tokens_out}
    """
    result: dict[str, dict] = {}
    total_calls = 0
    total_tokens_in = 0
    total_tokens_out = 0

    for svc in _SERVICES:
        key = f"llm:stats:{target_date}:{svc}"
        data = r.hgetall(key)
        if data:
            calls = int(data.get("calls", 0))
            tokens_in = int(data.get("tokens_in", 0))
            tokens_out = int(data.get("tokens_out", 0))
            result[svc] = {
                "calls": calls,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
            }
            total_calls += calls
            total_tokens_in += tokens_in
            total_tokens_out += tokens_out

    return {
        "date": target_date,
        "services": result,
        "total": {
            "calls": total_calls,
            "tokens_in": total_tokens_in,
            "tokens_out": total_tokens_out,
        },
    }


@router.get("/stats")
def get_today_stats(r: redis.Redis = Depends(get_redis_client)) -> dict:
    """오늘의 LLM 사용량 통계."""
    return get_stats(date.today().isoformat(), r)
