"""Macro Council Pipeline — 3단계 LLM 매크로 분석.

Step 1: Strategist (REASONING tier) — 시장 분석 + 섹터 신호
Step 2: Risk Analyst (REASONING tier) — 리스크 검증 + 포지션/손절 조정
Step 3: Chief Judge (THINKING tier) — 최종 판정 + 전략 추천

비용: ~$0.215/회 (DeepSeek×2 + Claude×1)
"""

import contextlib
import json
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from prime_jennie.domain.enums import SectorGroup, Sentiment, VixRegime
from prime_jennie.domain.macro import (
    GlobalSnapshot,
    MacroInsight,
    RiskFactor,
    SectorSignal,
)
from prime_jennie.infra.llm.base import BaseLLMProvider
from prime_jennie.infra.llm.factory import LLMFactory

from .schemas import (
    MACRO_CHIEF_JUDGE_SCHEMA,
    MACRO_RISK_ANALYST_SCHEMA,
    MACRO_STRATEGIST_SCHEMA,
)

logger = logging.getLogger(__name__)

# Valid strategies (Chief Judge output validation)
VALID_STRATEGIES = {
    "GOLDEN_CROSS",
    "RSI_REBOUND",
    "MOMENTUM",
    "MOMENTUM_CONTINUATION",
    "DIP_BUY",
    "WATCHLIST_CONVICTION",
    "VOLUME_BREAKOUT",
}

# Sector group names
SECTOR_GROUPS = {sg.value for sg in SectorGroup}


@dataclass
class CouncilInput:
    """Council 파이프라인 입력."""

    briefing_text: str = ""
    global_snapshot: GlobalSnapshot | None = None
    political_news: list[str] = field(default_factory=list)
    sector_momentum_text: str = ""
    target_date: date | None = None


@dataclass
class CouncilResult:
    """Council 파이프라인 출력."""

    insight: MacroInsight | None = None
    raw_outputs: dict[str, Any] = field(default_factory=dict)
    total_cost_usd: float = 0.0
    success: bool = False
    error: str = ""


class MacroCouncilPipeline:
    """3-Step LLM Macro Council.

    Args:
        reasoning_provider: Step 1, 2 LLM (default: REASONING tier)
        thinking_provider: Step 3 LLM (default: THINKING tier)
    """

    def __init__(
        self,
        reasoning_provider: BaseLLMProvider | None = None,
        thinking_provider: BaseLLMProvider | None = None,
    ):
        self._reasoning = reasoning_provider
        self._thinking = thinking_provider

    def _get_reasoning(self) -> BaseLLMProvider:
        if not self._reasoning:
            self._reasoning = LLMFactory.get_provider("reasoning")
        return self._reasoning

    def _get_thinking(self) -> BaseLLMProvider:
        if not self._thinking:
            self._thinking = LLMFactory.get_provider("thinking")
        return self._thinking

    async def run(self, input_data: CouncilInput) -> CouncilResult:
        """3단계 파이프라인 실행."""
        target_date = input_data.target_date or date.today()
        result = CouncilResult()

        # Build context
        context = self._build_context(input_data)
        if not context:
            result.error = "No input data available"
            return result

        # Step 1: Strategist
        try:
            step1 = await self._run_strategist(context)
            result.raw_outputs["strategist"] = step1
        except Exception as e:
            logger.error("Strategist failed: %s", e)
            result.error = f"Strategist failed: {e}"
            return result

        # Step 2: Risk Analyst
        try:
            step2 = await self._run_risk_analyst(context, step1)
            result.raw_outputs["risk_analyst"] = step2
        except Exception as e:
            logger.warning("Risk Analyst failed, using defaults: %s", e)
            step2 = self._default_risk_analyst(step1)
            result.raw_outputs["risk_analyst"] = step2

        # Step 3: Chief Judge
        try:
            step3 = await self._run_chief_judge(step1, step2)
            result.raw_outputs["chief_judge"] = step3
        except Exception as e:
            logger.warning("Chief Judge failed, merging Step 1+2: %s", e)
            step3 = self._fallback_merge(step1, step2)
            result.raw_outputs["chief_judge"] = step3

        # Build MacroInsight
        result.insight = self._build_insight(target_date, input_data, step1, step2, step3)
        result.success = True
        return result

    # --- Step Implementations ---

    async def _run_strategist(self, context: str) -> dict[str, Any]:
        """Step 1: 전략 분석."""
        system = (
            "당신은 한국 주식시장(KOSPI/KOSDAQ) 전문 매크로 전략가입니다. "
            "제공된 데이터를 바탕으로 시장 분석과 섹터 신호를 생성하세요. "
            "VIX는 참고용이며, 한국 시장 고유 요인을 우선 분석하세요. "
            "12시간 이상 오래된 뉴스는 가중치를 낮추세요."
        )
        return await self._get_reasoning().generate_json(
            prompt=context,
            schema=MACRO_STRATEGIST_SCHEMA,
            system=system,
            service="macro_council",
        )

    async def _run_risk_analyst(self, context: str, strategist: dict[str, Any]) -> dict[str, Any]:
        """Step 2: 리스크 검증."""
        prompt = (
            f"=== 원본 시장 데이터 ===\n{context}\n\n"
            f"=== 전략가 분석 결과 ===\n{json.dumps(strategist, ensure_ascii=False)}\n\n"
            "위 전략가 분석을 비판적으로 검증하고, 추가 리스크를 식별하세요. "
            "정치/지정학 리스크는 제공된 뉴스 기반으로만 평가하세요."
        )
        system = (
            "당신은 리스크 분석 전문가입니다. "
            "전략가가 과도하게 낙관적이거나 비관적인지 검증하세요. "
            "포지션 사이즈와 손절폭을 리스크에 비례하여 조정하세요."
        )
        return await self._get_reasoning().generate_json(
            prompt=prompt,
            schema=MACRO_RISK_ANALYST_SCHEMA,
            system=system,
            service="macro_council",
        )

    async def _run_chief_judge(
        self,
        strategist: dict[str, Any],
        risk_analyst: dict[str, Any],
    ) -> dict[str, Any]:
        """Step 3: 최종 판정."""
        prompt = (
            f"=== 전략가 분석 ===\n{json.dumps(strategist, ensure_ascii=False)}\n\n"
            f"=== 리스크 분석가 ===\n{json.dumps(risk_analyst, ensure_ascii=False)}\n\n"
            "두 분석을 종합하여 최종 트레이딩 판정을 내리세요.\n"
            f"사용 가능한 전략: {', '.join(sorted(VALID_STRATEGIES))}\n"
            f"14개 섹터: {', '.join(sorted(SECTOR_GROUPS))}"
        )
        system = (
            "당신은 한국 주식시장 수석 판정관입니다. "
            "전략가의 분석을 기본으로, 리스크 분석가의 우려를 반영하세요. "
            "의견 불일치 시 보수적 판단을 택하세요."
        )
        return await self._get_thinking().generate_json(
            prompt=prompt,
            schema=MACRO_CHIEF_JUDGE_SCHEMA,
            system=system,
            service="macro_council",
        )

    # --- Context Building ---

    def _build_context(self, input_data: CouncilInput) -> str:
        """LLM 프롬프트용 컨텍스트 구성."""
        parts = []

        if input_data.briefing_text:
            parts.append(f"=== 시장 브리핑 ===\n{input_data.briefing_text}")

        if input_data.global_snapshot:
            snap = input_data.global_snapshot
            snap_text = (
                f"=== 글로벌 매크로 ===\n"
                f"VIX: {snap.vix} ({snap.vix_regime})\n"
                f"USD/KRW: {snap.usd_krw}\n"
                f"KOSPI: {snap.kospi_index} ({snap.kospi_change_pct:+.2f}%)\n"
                f"KOSDAQ: {snap.kosdaq_index} ({snap.kosdaq_change_pct:+.2f}%)\n"
                f"외국인 KOSPI: {snap.kospi_foreign_net}억\n"
                f"기관 KOSPI: {snap.kospi_institutional_net}억"
            )
            parts.append(snap_text)

        if input_data.political_news:
            news = "\n".join(f"- {n}" for n in input_data.political_news[:15])
            parts.append(f"=== 정치/지정학 뉴스 ===\n{news}")

        if input_data.sector_momentum_text:
            parts.append(f"=== 섹터 모멘텀 ===\n{input_data.sector_momentum_text}")

        return "\n\n".join(parts)

    # --- Fallback Logic ---

    @staticmethod
    def _default_risk_analyst(strategist: dict[str, Any]) -> dict[str, Any]:
        """Risk Analyst 실패 시 기본값."""
        return {
            "risk_assessment": {
                "agree_with_sentiment": True,
                "adjusted_sentiment_score": strategist.get("sentiment_score", 50),
                "adjustment_reason": "Risk analyst unavailable, using strategist score",
            },
            "political_risk_level": "low",
            "political_risk_summary": "Risk analysis unavailable",
            "additional_risk_factors": [],
            "position_size_pct": 100,
            "stop_loss_adjust_pct": 100,
            "risk_reasoning": "Default values due to risk analyst failure",
        }

    @staticmethod
    def _fallback_merge(strategist: dict[str, Any], risk_analyst: dict[str, Any]) -> dict[str, Any]:
        """Chief Judge 실패 시 Step 1+2 병합."""
        risk = risk_analyst.get("risk_assessment", {})
        score = risk.get("adjusted_sentiment_score", strategist.get("sentiment_score", 50))
        return {
            "final_sentiment": strategist.get("overall_sentiment", "neutral"),
            "final_sentiment_score": score,
            "final_regime_hint": strategist.get("regime_hint", "unknown")[:50],
            "strategies_to_favor": [],
            "strategies_to_avoid": [],
            "sectors_to_favor": [],
            "sectors_to_avoid": [],
            "final_position_size_pct": risk_analyst.get("position_size_pct", 100),
            "final_stop_loss_adjust_pct": risk_analyst.get("stop_loss_adjust_pct", 100),
            "trading_reasoning": "Fallback: Chief Judge unavailable, merged Step 1+2",
            "council_consensus": "partial_disagree",
        }

    # --- Insight Building ---

    def _build_insight(
        self,
        target_date: date,
        input_data: CouncilInput,
        strategist: dict[str, Any],
        risk_analyst: dict[str, Any],
        chief_judge: dict[str, Any],
    ) -> MacroInsight:
        """3개 출력을 MacroInsight로 변환."""
        # Sentiment
        sentiment_str = chief_judge.get("final_sentiment", "neutral")
        try:
            sentiment = Sentiment(sentiment_str)
        except ValueError:
            sentiment = Sentiment.NEUTRAL

        # Score (clamped 0-100)
        score = max(0, min(100, chief_judge.get("final_sentiment_score", 50)))

        # Regime hint (truncated)
        regime_hint = (chief_judge.get("final_regime_hint") or "unknown")[:50]

        # Sector signals from strategist
        sector_signals = []
        for name, sig in strategist.get("sector_signals", {}).items():
            try:
                sg = SectorGroup(name)
                sector_signals.append(SectorSignal(sector_group=sg, signal=sig.upper()))
            except ValueError:
                continue

        # Risk factors
        risk_factors = [RiskFactor(name=rf, severity="MID") for rf in strategist.get("risk_factors", [])[:10]]

        # Sectors to favor/avoid (validate against SectorGroup)
        favor = _parse_sector_groups(chief_judge.get("sectors_to_favor", []))
        avoid = _parse_sector_groups(chief_judge.get("sectors_to_avoid", []))

        # Position/StopLoss clamped
        pos_pct = max(50, min(130, chief_judge.get("final_position_size_pct", 100)))
        sl_pct = max(80, min(150, chief_judge.get("final_stop_loss_adjust_pct", 100)))

        # VIX
        vix_val = None
        vix_regime = VixRegime.NORMAL
        if input_data.global_snapshot:
            vix_val = input_data.global_snapshot.vix
            with contextlib.suppress(ValueError):
                vix_regime = VixRegime(input_data.global_snapshot.vix_regime)

        return MacroInsight(
            insight_date=target_date,
            sentiment=sentiment,
            sentiment_score=score,
            regime_hint=regime_hint,
            sector_signals=sector_signals,
            risk_factors=risk_factors,
            sectors_to_favor=favor,
            sectors_to_avoid=avoid,
            position_size_pct=pos_pct,
            stop_loss_adjust_pct=sl_pct,
            political_risk_level=risk_analyst.get("political_risk_level", "low"),
            vix_value=vix_val,
            vix_regime=vix_regime,
            usd_krw=input_data.global_snapshot.usd_krw if input_data.global_snapshot else None,
            kospi_index=input_data.global_snapshot.kospi_index if input_data.global_snapshot else None,
            kosdaq_index=input_data.global_snapshot.kosdaq_index if input_data.global_snapshot else None,
        )


def _parse_sector_groups(names: list[str]) -> list[SectorGroup]:
    """문자열 리스트 → SectorGroup enum (유효한 것만)."""
    result = []
    for name in names:
        try:
            result.append(SectorGroup(name))
        except ValueError:
            continue
    return result
