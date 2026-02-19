"""Macro Council Pipeline 단위 테스트."""

import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prime_jennie.domain.enums import SectorGroup, Sentiment, VixRegime
from prime_jennie.domain.macro import GlobalSnapshot, MacroInsight
from prime_jennie.services.council.pipeline import (
    CouncilInput,
    CouncilResult,
    MacroCouncilPipeline,
    VALID_STRATEGIES,
    _parse_sector_groups,
)
from prime_jennie.services.council.schemas import (
    MACRO_CHIEF_JUDGE_SCHEMA,
    MACRO_RISK_ANALYST_SCHEMA,
    MACRO_STRATEGIST_SCHEMA,
)


@pytest.fixture(autouse=True)
def _clear_config_cache():
    from prime_jennie.domain.config import get_config
    get_config.cache_clear()
    yield
    get_config.cache_clear()


def _mock_strategist_output():
    return {
        "overall_sentiment": "neutral_to_bullish",
        "sentiment_score": 65,
        "regime_hint": "Trend_Following",
        "sector_signals": {
            "반도체/IT": "bullish",
            "자동차": "neutral",
            "바이오/헬스케어": "bearish",
        },
        "risk_factors": ["미중 무역갈등", "원화 약세"],
        "opportunity_factors": ["반도체 수출 호조", "외국인 매수세"],
        "investor_flow_analysis": "외국인 3일 연속 순매수",
    }


def _mock_risk_analyst_output():
    return {
        "risk_assessment": {
            "agree_with_sentiment": True,
            "adjusted_sentiment_score": 60,
            "adjustment_reason": "원화 약세 리스크 반영",
        },
        "political_risk_level": "medium",
        "political_risk_summary": "미중 관세 갈등 진행 중",
        "additional_risk_factors": ["엔화 강세 전환 가능성"],
        "position_size_pct": 90,
        "stop_loss_adjust_pct": 110,
        "risk_reasoning": "전반적으로 동의하나 리스크 반영 필요",
    }


def _mock_chief_judge_output():
    return {
        "final_sentiment": "neutral_to_bullish",
        "final_sentiment_score": 62,
        "final_regime_hint": "Selective_Buying",
        "strategies_to_favor": ["GOLDEN_CROSS", "DIP_BUY"],
        "strategies_to_avoid": ["MOMENTUM_CONTINUATION"],
        "sectors_to_favor": ["반도체/IT"],
        "sectors_to_avoid": ["바이오/헬스케어"],
        "final_position_size_pct": 95,
        "final_stop_loss_adjust_pct": 105,
        "trading_reasoning": "선별적 매수 접근 권장",
        "council_consensus": "agree",
    }


class TestSchemas:
    """JSON Schema 구조 검증."""

    def test_strategist_schema_required_fields(self):
        assert "overall_sentiment" in MACRO_STRATEGIST_SCHEMA["required"]
        assert "sentiment_score" in MACRO_STRATEGIST_SCHEMA["required"]

    def test_risk_analyst_schema_required_fields(self):
        assert "risk_assessment" in MACRO_RISK_ANALYST_SCHEMA["required"]
        assert "political_risk_level" in MACRO_RISK_ANALYST_SCHEMA["required"]

    def test_chief_judge_schema_required_fields(self):
        assert "final_sentiment" in MACRO_CHIEF_JUDGE_SCHEMA["required"]
        assert "council_consensus" in MACRO_CHIEF_JUDGE_SCHEMA["required"]

    def test_sentiment_enum_values(self):
        allowed = MACRO_STRATEGIST_SCHEMA["properties"]["overall_sentiment"]["enum"]
        assert "bullish" in allowed
        assert "bearish" in allowed
        assert "neutral" in allowed

    def test_consensus_enum_values(self):
        allowed = MACRO_CHIEF_JUDGE_SCHEMA["properties"]["council_consensus"]["enum"]
        assert "strong_agree" in allowed
        assert "disagree" in allowed


class TestParseSectorGroups:
    def test_valid_sectors(self):
        result = _parse_sector_groups(["반도체/IT", "금융"])
        assert SectorGroup.SEMICONDUCTOR_IT in result
        assert SectorGroup.FINANCE in result

    def test_invalid_sectors_skipped(self):
        result = _parse_sector_groups(["반도체/IT", "존재하지않는섹터"])
        assert len(result) == 1

    def test_empty_input(self):
        assert _parse_sector_groups([]) == []


class TestCouncilPipeline:
    """MacroCouncilPipeline 통합 테스트 (mock LLM)."""

    @pytest.mark.asyncio
    async def test_full_pipeline_success(self):
        """3단계 파이프라인 정상 실행."""
        mock_reasoning = AsyncMock()
        mock_reasoning.generate_json = AsyncMock(
            side_effect=[_mock_strategist_output(), _mock_risk_analyst_output()]
        )
        mock_thinking = AsyncMock()
        mock_thinking.generate_json = AsyncMock(
            return_value=_mock_chief_judge_output()
        )

        pipeline = MacroCouncilPipeline(mock_reasoning, mock_thinking)
        input_data = CouncilInput(
            briefing_text="오늘 KOSPI는 2,650선에서 상승 출발...",
            target_date=date(2026, 2, 19),
        )

        result = await pipeline.run(input_data)

        assert result.success
        assert result.insight is not None
        assert result.insight.sentiment == Sentiment.NEUTRAL_TO_BULLISH
        assert result.insight.sentiment_score == 62
        assert result.insight.regime_hint == "Selective_Buying"
        assert result.insight.position_size_pct == 95
        assert result.insight.political_risk_level == "medium"

    @pytest.mark.asyncio
    async def test_risk_analyst_failure_uses_defaults(self):
        """Step 2 실패 → 기본값 사용."""
        mock_reasoning = AsyncMock()
        mock_reasoning.generate_json = AsyncMock(
            side_effect=[_mock_strategist_output(), Exception("API timeout")]
        )
        mock_thinking = AsyncMock()
        mock_thinking.generate_json = AsyncMock(
            return_value=_mock_chief_judge_output()
        )

        pipeline = MacroCouncilPipeline(mock_reasoning, mock_thinking)
        input_data = CouncilInput(briefing_text="테스트 브리핑")

        result = await pipeline.run(input_data)

        assert result.success
        # Risk analyst used defaults
        assert result.raw_outputs["risk_analyst"]["position_size_pct"] == 100

    @pytest.mark.asyncio
    async def test_chief_judge_failure_merges_steps(self):
        """Step 3 실패 → Step 1+2 병합."""
        mock_reasoning = AsyncMock()
        mock_reasoning.generate_json = AsyncMock(
            side_effect=[_mock_strategist_output(), _mock_risk_analyst_output()]
        )
        mock_thinking = AsyncMock()
        mock_thinking.generate_json = AsyncMock(side_effect=Exception("Opus down"))

        pipeline = MacroCouncilPipeline(mock_reasoning, mock_thinking)
        input_data = CouncilInput(briefing_text="테스트 브리핑")

        result = await pipeline.run(input_data)

        assert result.success
        assert result.raw_outputs["chief_judge"]["council_consensus"] == "partial_disagree"

    @pytest.mark.asyncio
    async def test_strategist_failure_aborts(self):
        """Step 1 실패 → 전체 중단."""
        mock_reasoning = AsyncMock()
        mock_reasoning.generate_json = AsyncMock(side_effect=Exception("DeepSeek down"))

        pipeline = MacroCouncilPipeline(mock_reasoning, AsyncMock())
        input_data = CouncilInput(briefing_text="테스트")

        result = await pipeline.run(input_data)

        assert not result.success
        assert "Strategist failed" in result.error

    @pytest.mark.asyncio
    async def test_empty_input_returns_error(self):
        """입력 데이터 없음 → 에러."""
        pipeline = MacroCouncilPipeline(AsyncMock(), AsyncMock())
        input_data = CouncilInput()

        result = await pipeline.run(input_data)

        assert not result.success
        assert "No input data" in result.error


class TestInsightBuilding:
    """MacroInsight 변환 검증."""

    @pytest.mark.asyncio
    async def test_sectors_parsed_correctly(self):
        mock_reasoning = AsyncMock()
        mock_reasoning.generate_json = AsyncMock(
            side_effect=[_mock_strategist_output(), _mock_risk_analyst_output()]
        )
        mock_thinking = AsyncMock()
        mock_thinking.generate_json = AsyncMock(
            return_value=_mock_chief_judge_output()
        )

        pipeline = MacroCouncilPipeline(mock_reasoning, mock_thinking)
        result = await pipeline.run(CouncilInput(briefing_text="test"))

        insight = result.insight
        assert SectorGroup.SEMICONDUCTOR_IT in insight.sectors_to_favor
        assert SectorGroup.BIO_HEALTH in insight.sectors_to_avoid

    @pytest.mark.asyncio
    async def test_score_clamped(self):
        """점수가 0-100 범위로 클램핑."""
        judge = _mock_chief_judge_output()
        judge["final_sentiment_score"] = 150  # over 100

        mock_reasoning = AsyncMock()
        mock_reasoning.generate_json = AsyncMock(
            side_effect=[_mock_strategist_output(), _mock_risk_analyst_output()]
        )
        mock_thinking = AsyncMock()
        mock_thinking.generate_json = AsyncMock(return_value=judge)

        pipeline = MacroCouncilPipeline(mock_reasoning, mock_thinking)
        result = await pipeline.run(CouncilInput(briefing_text="test"))

        assert result.insight.sentiment_score == 100

    @pytest.mark.asyncio
    async def test_position_size_clamped(self):
        judge = _mock_chief_judge_output()
        judge["final_position_size_pct"] = 200

        mock_reasoning = AsyncMock()
        mock_reasoning.generate_json = AsyncMock(
            side_effect=[_mock_strategist_output(), _mock_risk_analyst_output()]
        )
        mock_thinking = AsyncMock()
        mock_thinking.generate_json = AsyncMock(return_value=judge)

        pipeline = MacroCouncilPipeline(mock_reasoning, mock_thinking)
        result = await pipeline.run(CouncilInput(briefing_text="test"))

        assert result.insight.position_size_pct == 130  # clamped to max

    @pytest.mark.asyncio
    async def test_global_snapshot_integrated(self):
        from datetime import datetime, timezone

        snap = GlobalSnapshot(
            snapshot_date=date(2026, 2, 19),
            timestamp=datetime.now(timezone.utc),
            vix=18.5,
            vix_regime=VixRegime.NORMAL,
            usd_krw=1320.5,
            kospi_index=2650.0,
            kospi_change_pct=0.5,
            kosdaq_index=840.0,
            kosdaq_change_pct=-0.3,
            kospi_foreign_net=1500,
            kospi_institutional_net=800,
        )

        mock_reasoning = AsyncMock()
        mock_reasoning.generate_json = AsyncMock(
            side_effect=[_mock_strategist_output(), _mock_risk_analyst_output()]
        )
        mock_thinking = AsyncMock()
        mock_thinking.generate_json = AsyncMock(
            return_value=_mock_chief_judge_output()
        )

        pipeline = MacroCouncilPipeline(mock_reasoning, mock_thinking)
        result = await pipeline.run(
            CouncilInput(briefing_text="test", global_snapshot=snap)
        )

        assert result.insight.vix_value == 18.5
        assert result.insight.usd_krw == 1320.5
        assert result.insight.kospi_index == 2650.0


class TestDefaultRiskAnalyst:
    def test_uses_strategist_score(self):
        strategist = _mock_strategist_output()
        default = MacroCouncilPipeline._default_risk_analyst(strategist)
        assert default["risk_assessment"]["adjusted_sentiment_score"] == 65
        assert default["position_size_pct"] == 100


class TestFallbackMerge:
    def test_produces_valid_output(self):
        merged = MacroCouncilPipeline._fallback_merge(
            _mock_strategist_output(), _mock_risk_analyst_output()
        )
        assert merged["final_sentiment"] == "neutral_to_bullish"
        assert merged["council_consensus"] == "partial_disagree"
        assert merged["final_position_size_pct"] == 90

    def test_regime_hint_truncated(self):
        strat = _mock_strategist_output()
        strat["regime_hint"] = "A" * 100
        merged = MacroCouncilPipeline._fallback_merge(strat, _mock_risk_analyst_output())
        assert len(merged["final_regime_hint"]) <= 50


class TestValidStrategies:
    def test_contains_all_strategies(self):
        expected = {
            "GOLDEN_CROSS", "RSI_REBOUND", "MOMENTUM",
            "MOMENTUM_CONTINUATION", "DIP_BUY",
            "WATCHLIST_CONVICTION", "VOLUME_BREAKOUT",
        }
        assert expected == VALID_STRATEGIES
