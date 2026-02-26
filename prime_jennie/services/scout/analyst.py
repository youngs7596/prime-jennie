"""Scout Phase 4: Unified Analyst — 1-pass LLM + 코드 기반 risk_tag.

3→1 LLM 호출 통합 (기존 Hunter+Debate+Judge → Unified Analyst).
±15pt 가드레일, 코드 기반 risk_tag 분류, DISTRIBUTION_RISK Veto Power.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from prime_jennie.domain.config import get_config
from prime_jennie.domain.enums import RiskTag, TradeTier
from prime_jennie.domain.macro import TradingContext
from prime_jennie.domain.scoring import HybridScore, QuantScore
from prime_jennie.infra.llm.base import BaseLLMProvider

from .enrichment import EnrichedCandidate

logger = logging.getLogger(__name__)

# LLM 응답 스키마
ANALYST_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "minimum": 0, "maximum": 100},
        "grade": {"type": "string", "enum": ["S", "A", "B", "C", "D"]},
        "reason": {"type": "string", "minLength": 20, "maxLength": 500},
    },
    "required": ["score", "grade", "reason"],
}


async def run_analyst(
    quant: QuantScore,
    candidate: EnrichedCandidate,
    context: TradingContext,
    llm: BaseLLMProvider,
) -> HybridScore:
    """Phase 4: Unified Analyst (1-pass LLM + 코드 기반 risk_tag).

    Args:
        quant: Phase 3 Quant Score
        candidate: Phase 2 보강 데이터
        context: 트레이딩 컨텍스트 (시장 국면 등)
        llm: REASONING tier LLM provider

    Returns:
        HybridScore (최종 평가)
    """
    config = get_config().scoring

    # 1. LLM 호출
    try:
        llm_result = await _call_llm(quant, candidate, context, llm)
    except Exception as e:
        logger.error("[%s] LLM call failed: %s — using quant score as fallback", quant.stock_code, e)
        llm_result = {
            "score": int(quant.total_score),
            "grade": _score_to_grade(quant.total_score),
            "reason": f"LLM fallback: {str(e)[:100]}",
        }

    raw_score = float(llm_result["score"])

    # 2. ±15pt 가드레일
    clamped = _clamp_score(raw_score, quant.total_score, config.llm_clamp_range)

    # 3. 코드 기반 risk_tag
    risk_tag = classify_risk_tag(quant, candidate)

    # 4. Veto Power + Trade Tier
    veto_applied = risk_tag == RiskTag.DISTRIBUTION_RISK
    trade_tier = TradeTier.BLOCKED if veto_applied else _assign_trade_tier(clamped)
    is_tradable = trade_tier != TradeTier.BLOCKED

    now = datetime.now(UTC)

    return HybridScore(
        stock_code=quant.stock_code,
        stock_name=quant.stock_name,
        quant_score=quant.total_score,
        llm_score=raw_score,
        hybrid_score=round(clamped, 1),
        risk_tag=risk_tag,
        trade_tier=trade_tier,
        is_tradable=is_tradable,
        veto_applied=veto_applied,
        llm_grade=llm_result["grade"],
        llm_reason=llm_result["reason"],
        scored_at=now,
    )


def classify_risk_tag(quant: QuantScore, candidate: EnrichedCandidate) -> RiskTag:
    """코드 기반 risk_tag 분류 — LLM 100% CAUTION 편향 해소.

    Rules:
        DISTRIBUTION_RISK: 고가+과열+수급악화 (거래 차단)
        CAUTION: 고가+과열 또는 수급 급감
        BULLISH: 모멘텀+수급+품질 양호
        NEUTRAL: 기본값
    """
    it = candidate.investor_trading
    prices = candidate.daily_prices
    snapshot = candidate.snapshot

    # DISTRIBUTION_RISK: 고점 부근 + RSI 과열 + 수급 악화
    if snapshot and prices and len(prices) >= 20:
        closes = [p.close_price for p in prices]
        rsi = _compute_rsi_quick(closes)

        # 52주 고점 대비
        high_52w = snapshot.high_52w or max(closes)
        drawdown_pct = (snapshot.price / high_52w - 1) * 100 if high_52w > 0 else -100

        # 수급 악화 (3배 강화: 소규모 이탈은 DISTRIBUTION_RISK 미발동)
        foreign_negative = it and it.foreign_net_buy_sum < -3e9
        inst_negative = it and it.institution_net_buy_sum < -3e9

        if drawdown_pct > -3 and rsi and rsi > 70 and foreign_negative and inst_negative:
            return RiskTag.DISTRIBUTION_RISK

    # CAUTION: 극단 과매수 OR 수급 급감
    if snapshot and prices and len(prices) >= 14:
        closes = [p.close_price for p in prices]
        rsi = _compute_rsi_quick(closes)
        if rsi and rsi > 80:
            return RiskTag.CAUTION

    if it and it.foreign_net_buy_sum < -3e9:
        return RiskTag.CAUTION

    # BULLISH: 양호한 조건
    if quant.momentum_score >= 12 and quant.supply_demand_score >= 12 and quant.quality_score >= 10:
        return RiskTag.BULLISH

    return RiskTag.NEUTRAL


async def _call_llm(
    quant: QuantScore,
    candidate: EnrichedCandidate,
    context: TradingContext,
    llm: BaseLLMProvider,
) -> dict[str, Any]:
    """LLM 호출 (1-pass Unified Analyst)."""
    prompt = _build_prompt(quant, candidate, context)

    result = await llm.generate_json(
        prompt=prompt,
        schema=ANALYST_RESPONSE_SCHEMA,
        system="당신은 한국 주식 애널리스트입니다. 주어진 데이터를 분석하고 종합 점수를 매겨주세요.",
        temperature=0.3,
        max_tokens=4096,
        service="scout",
    )
    return result


def _build_prompt(
    quant: QuantScore,
    candidate: EnrichedCandidate,
    context: TradingContext,
) -> str:
    """Unified Analyst 프롬프트 생성."""
    master = candidate.master
    snap = candidate.snapshot
    ft = candidate.financial_trend
    it = candidate.investor_trading

    lines = [
        f"## 종목 분석: {master.stock_name} ({master.stock_code})",
        f"시장: {master.market}, 섹터: {master.sector_group or '미분류'}",
        f"시장 국면: {context.market_regime}",
        "",
        f"### Quant Score: {quant.total_score:.1f}/100",
        f"  모멘텀: {quant.momentum_score:.1f}/20",
        f"  품질: {quant.quality_score:.1f}/20",
        f"  가치: {quant.value_score:.1f}/20",
        f"  기술: {quant.technical_score:.1f}/10",
        f"  뉴스: {quant.news_score:.1f}/10",
        f"  수급: {quant.supply_demand_score:.1f}/20",
    ]

    if snap:
        lines.extend(
            [
                "",
                "### 현재가 정보",
                f"  가격: {snap.price:,}원 (전일비: {snap.change_pct:+.2f}%)",
                f"  PER: {snap.per or 'N/A'}, PBR: {snap.pbr or 'N/A'}",
            ]
        )

    if ft:
        lines.extend(
            [
                "",
                "### 재무",
                f"  ROE: {ft.roe or 'N/A'}%, PER: {ft.per or 'N/A'}, PBR: {ft.pbr or 'N/A'}",
            ]
        )

    if it:
        lines.extend(
            [
                "",
                "### 수급 (60일)",
                f"  외인 순매수: {it.foreign_net_buy_sum:,.0f}",
                f"  기관 순매수: {it.institution_net_buy_sum:,.0f}",
                f"  외인 비율 추세: {it.foreign_ratio_trend or 'N/A'}",
            ]
        )

    # RAG 뉴스 컨텍스트 주입
    skip_news = {"뉴스 DB 미연결", "최근 관련 뉴스 없음", "뉴스 검색 오류"}
    if candidate.rag_news_context and candidate.rag_news_context not in skip_news:
        lines.extend(["", "### 최근 뉴스 (RAG)", f"  {candidate.rag_news_context}"])

    lines.extend(
        [
            "",
            "### 요청",
            "위 데이터를 종합적으로 분석하여 0-100점 사이의 투자 매력도 점수를 부여하세요.",
            "score, grade(S/A/B/C/D), reason(한국어)을 JSON으로 응답하세요.",
        ]
    )

    return "\n".join(lines)


# ─── Helpers ─────────────────────────────────────────────────────


def _clamp_score(raw: float, quant: float, clamp_range: int) -> float:
    """±clamp_range pt 가드레일."""
    lo = max(0.0, quant - clamp_range)
    hi = min(100.0, quant + clamp_range)
    return max(lo, min(hi, raw))


def _assign_trade_tier(hybrid_score: float) -> TradeTier:
    """점수 기반 거래 등급 배정."""
    if hybrid_score >= 60:
        return TradeTier.TIER1
    elif hybrid_score >= 40:
        return TradeTier.TIER2
    else:
        return TradeTier.BLOCKED


def _score_to_grade(score: float) -> str:
    if score >= 80:
        return "S"
    elif score >= 65:
        return "A"
    elif score >= 50:
        return "B"
    elif score >= 35:
        return "C"
    return "D"


def _compute_rsi_quick(closes: list[int | float], period: int = 14) -> float | None:
    """간소화 RSI 계산."""
    if len(closes) < period + 1:
        return None

    gains = []
    losses = []
    for i in range(len(closes) - period, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(0, delta))
        losses.append(max(0, -delta))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        return 100.0
    return 100 - (100 / (1 + avg_gain / avg_loss))
