"""LLM Stats API — LLM 사용량 통계 + 기능별 LLM 매핑."""

from datetime import date

import redis
from fastapi import APIRouter, Depends

from prime_jennie.domain.config import get_config
from prime_jennie.infra.observability.metrics import get_llm_monthly_stats
from prime_jennie.services.deps import get_redis_client

router = APIRouter(prefix="/llm", tags=["llm"])

# 추적 대상 서비스 목록
_SERVICES = ["scout", "briefing", "macro_council", "news_analysis", "wsj_summary", "unknown"]

# 기능별 LLM 매핑 (정적 정보)
_FEATURE_MAP = [
    {
        "service": "news_analysis",
        "name": "뉴스 감성 분석",
        "tier": "FAST",
        "frequency": "실시간 (배치 20건)",
    },
    {
        "service": "scout",
        "name": "Scout 종목 분석",
        "tier": "REASONING",
        "frequency": "매시 (08:30~14:30)",
    },
    {
        "service": "macro_council",
        "name": "Macro Council",
        "tier": "REASONING + Claude",
        "frequency": "1일 2회 (07:50, 11:50)",
    },
    {
        "service": "briefing",
        "name": "데일리 브리핑",
        "tier": "Claude",
        "frequency": "1일 1회",
    },
    {
        "service": "wsj_summary",
        "name": "WSJ 뉴스 요약",
        "tier": "Claude",
        "frequency": "1일 1회",
    },
]

# Provider → 표시명
_PROVIDER_DISPLAY = {
    "ollama": "vLLM (EXAONE)",
    "deepseek_cloud": "DeepSeek (Cloud)",
    "claude": "Claude",
    "openai": "OpenAI",
    "gemini": "Gemini",
}

# Tier → config에서 모델명 가져오기
_TIER_MODEL = {
    "FAST": lambda cfg: "EXAONE-4.0-32B-AWQ" if cfg.tier_fast_provider == "ollama" else cfg.openai_model,
    "REASONING": lambda cfg: "deepseek-reasoner" if "deepseek" in cfg.tier_reasoning_provider else cfg.openai_model,
    "Claude": lambda cfg: cfg.claude_model,
    "REASONING + Claude": lambda cfg: f"deepseek-reasoner + {cfg.claude_model}",
}


def _resolve_provider_display(tier: str, cfg) -> str:
    """Tier 문자열에서 표시용 provider명 생성."""
    if tier == "REASONING + Claude":
        reasoning = _PROVIDER_DISPLAY.get(cfg.tier_reasoning_provider, cfg.tier_reasoning_provider)
        return f"{reasoning} + Claude"
    if tier == "Claude":
        return "Claude"
    provider_key = {
        "FAST": cfg.tier_fast_provider,
        "REASONING": cfg.tier_reasoning_provider,
    }.get(tier, "unknown")
    return _PROVIDER_DISPLAY.get(provider_key, provider_key)


def _build_daily_stats(target_date: str, r: redis.Redis) -> dict:
    """특정 날짜의 LLM 사용량 통계 조회."""
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


@router.get("/features")
def get_features() -> list[dict]:
    """기능별 LLM 매핑 정보."""
    cfg = get_config().llm
    result = []
    for feat in _FEATURE_MAP:
        model_fn = _TIER_MODEL.get(feat["tier"])
        result.append(
            {
                **feat,
                "provider": _resolve_provider_display(feat["tier"], cfg),
                "model": model_fn(cfg) if model_fn else "unknown",
            }
        )
    return result


# 주의: /stats/monthly* 라우트를 /stats/{target_date} 앞에 배치 (path param 충돌 방지)
@router.get("/stats/monthly/{target_month}")
def get_monthly_stats(
    target_month: str,
    r: redis.Redis = Depends(get_redis_client),
) -> dict:
    """월별 LLM 사용량 통계. target_month: YYYY-MM"""
    services = get_llm_monthly_stats(r, target_month)

    total_calls = sum(s["calls"] for s in services.values())
    total_tokens_in = sum(s["tokens_in"] for s in services.values())
    total_tokens_out = sum(s["tokens_out"] for s in services.values())

    return {
        "month": target_month,
        "services": services,
        "total": {
            "calls": total_calls,
            "tokens_in": total_tokens_in,
            "tokens_out": total_tokens_out,
        },
    }


@router.get("/stats/monthly")
def get_current_month_stats(r: redis.Redis = Depends(get_redis_client)) -> dict:
    """이번 달 LLM 사용량 통계."""
    return get_monthly_stats(date.today().strftime("%Y-%m"), r)


@router.get("/stats/{target_date}")
def get_stats(
    target_date: str,
    r: redis.Redis = Depends(get_redis_client),
) -> dict:
    """특정 날짜의 LLM 사용량 통계."""
    return _build_daily_stats(target_date, r)


@router.get("/stats")
def get_today_stats(r: redis.Redis = Depends(get_redis_client)) -> dict:
    """오늘의 LLM 사용량 통계."""
    return _build_daily_stats(date.today().isoformat(), r)
