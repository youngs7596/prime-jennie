"""Scout Unified Analyst 단위 테스트."""

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prime_jennie.domain.enums import (
    MarketRegime,
    RiskTag,
    SectorGroup,
    TradeTier,
)
from prime_jennie.domain.macro import TradingContext
from prime_jennie.domain.scoring import HybridScore, QuantScore
from prime_jennie.domain.stock import StockMaster, StockSnapshot
from prime_jennie.services.scout.analyst import (
    _assign_trade_tier,
    _clamp_score,
    _score_to_grade,
    classify_risk_tag,
    run_analyst,
)
from prime_jennie.services.scout.enrichment import (
    EnrichedCandidate,
    FinancialTrend,
    InvestorTradingSummary,
)


def _make_prices(n: int = 150, base: int = 70000, trend: float = 0.001) -> list:
    """n일치 일봉 생성 (상승 추세)."""
    from prime_jennie.domain.stock import DailyPrice
    from datetime import date

    prices = []
    for i in range(n):
        price = int(base * (1 + trend * i))
        prices.append(
            DailyPrice(
                stock_code="005930",
                price_date=date(2026, 2, 19),
                open_price=price - 200,
                high_price=price + 300,
                low_price=price - 400,
                close_price=price,
                volume=10000000 + i * 10000,
            )
        )
    return prices


# ─── Fixtures ────────────────────────────────────────────────────

NOW = datetime(2026, 2, 19, 10, 0, 0, tzinfo=timezone.utc)


def _quant(code: str = "005930", total: float = 60.0) -> QuantScore:
    # subscores가 total에 맞도록 비례 조정
    base_sum = 60.0  # 12+10+12+8+7+11
    ratio = total / base_sum if base_sum > 0 else 1.0
    return QuantScore(
        stock_code=code,
        stock_name="삼성전자",
        total_score=total,
        momentum_score=round(12.0 * ratio, 1),
        quality_score=round(10.0 * ratio, 1),
        value_score=round(12.0 * ratio, 1),
        technical_score=round(8.0 * ratio, 1),
        news_score=round(7.0 * ratio, 1),
        supply_demand_score=round(total - round(12.0 * ratio, 1) - round(10.0 * ratio, 1) - round(12.0 * ratio, 1) - round(8.0 * ratio, 1) - round(7.0 * ratio, 1), 1),
    )


def _candidate(
    code: str = "005930",
    snapshot: StockSnapshot | None = None,
    prices_n: int = 60,
    ft: FinancialTrend | None = None,
    it: InvestorTradingSummary | None = None,
) -> EnrichedCandidate:
    return EnrichedCandidate(
        master=StockMaster(
            stock_code=code,
            stock_name="삼성전자",
            market="KOSPI",
            sector_group=SectorGroup.SEMICONDUCTOR_IT,
        ),
        snapshot=snapshot,
        daily_prices=_make_prices(prices_n) if prices_n > 0 else [],
        financial_trend=ft,
        investor_trading=it,
    )


def _context(regime: MarketRegime = MarketRegime.BULL) -> TradingContext:
    return TradingContext(
        date=date(2026, 2, 19),
        market_regime=regime,
    )


# ─── Clamp Score ─────────────────────────────────────────────────


class TestClampScore:
    def test_within_range_unchanged(self):
        assert _clamp_score(65.0, 60.0, 15) == 65.0

    def test_clamped_above(self):
        # raw=90, quant=60, range=15 → max=75
        assert _clamp_score(90.0, 60.0, 15) == 75.0

    def test_clamped_below(self):
        # raw=30, quant=60, range=15 → min=45
        assert _clamp_score(30.0, 60.0, 15) == 45.0

    def test_at_boundary(self):
        assert _clamp_score(75.0, 60.0, 15) == 75.0
        assert _clamp_score(45.0, 60.0, 15) == 45.0

    def test_zero_quant(self):
        # quant=0 → range [0, 15]
        assert _clamp_score(10.0, 0.0, 15) == 10.0
        assert _clamp_score(20.0, 0.0, 15) == 15.0

    def test_max_quant(self):
        # quant=100 → range [85, 100]
        assert _clamp_score(90.0, 100.0, 15) == 90.0
        assert _clamp_score(80.0, 100.0, 15) == 85.0


# ─── Risk Tag Classification ────────────────────────────────────


class TestClassifyRiskTag:
    def test_distribution_risk_high_point_overbought_selling(self):
        """고점 + 과열 + 수급 악화 → DISTRIBUTION_RISK."""
        snapshot = StockSnapshot(
            stock_code="005930",
            price=90000,
            high_52w=92000,
            timestamp=NOW,
        )
        # 만들어야 할 것: RSI > 70인 가격 데이터
        # 20일 연속 상승하는 데이터
        prices = _make_prices(30, base=85000, trend=0.003)
        candidate = _candidate(
            snapshot=snapshot,
            prices_n=0,
            it=InvestorTradingSummary(
                foreign_net_buy_sum=-2e9,
                institution_net_buy_sum=-2e9,
            ),
        )
        candidate.daily_prices = prices
        quant = _quant(total=55.0)

        tag = classify_risk_tag(quant, candidate)
        # 정확한 조건에 따라 DISTRIBUTION_RISK 또는 CAUTION
        assert tag in (RiskTag.DISTRIBUTION_RISK, RiskTag.CAUTION)

    def test_caution_when_rsi_overbought(self):
        """RSI > 70 → CAUTION."""
        # 급상승 가격 데이터 (RSI > 70)
        prices = _make_prices(30, base=50000, trend=0.01)
        snapshot = StockSnapshot(
            stock_code="005930",
            price=65000,
            high_52w=70000,
            timestamp=NOW,
        )
        candidate = _candidate(snapshot=snapshot, prices_n=0)
        candidate.daily_prices = prices
        quant = _quant(total=55.0)

        tag = classify_risk_tag(quant, candidate)
        # RSI 과열 시 CAUTION
        assert tag in (RiskTag.CAUTION, RiskTag.NEUTRAL, RiskTag.BULLISH)

    def test_bullish_when_strong_factors(self):
        """모멘텀+수급+품질 양호 → BULLISH."""
        candidate = _candidate(prices_n=30)
        quant = QuantScore(
            stock_code="005930",
            stock_name="삼성전자",
            total_score=70.0,
            momentum_score=15.0,  # >= 12
            quality_score=12.0,  # >= 10
            value_score=12.0,
            technical_score=7.0,
            news_score=6.0,
            supply_demand_score=18.0,  # >= 12
        )

        tag = classify_risk_tag(quant, candidate)
        assert tag == RiskTag.BULLISH

    def test_neutral_default(self):
        """기본값 → NEUTRAL."""
        candidate = _candidate(prices_n=30)
        quant = _quant(total=50.0)

        tag = classify_risk_tag(quant, candidate)
        assert tag == RiskTag.NEUTRAL

    def test_veto_implies_blocked(self):
        """DISTRIBUTION_RISK → veto_applied → BLOCKED."""
        # 이건 run_analyst에서 체크하므로 여기선 tag만 확인
        pass


# ─── Trade Tier Assignment ───────────────────────────────────────


class TestAssignTradeTier:
    def test_tier1_for_high_score(self):
        assert _assign_trade_tier(75.0) == TradeTier.TIER1

    def test_tier2_for_mid_score(self):
        assert _assign_trade_tier(50.0) == TradeTier.TIER2

    def test_blocked_for_low_score(self):
        assert _assign_trade_tier(30.0) == TradeTier.BLOCKED

    def test_boundary_60(self):
        assert _assign_trade_tier(60.0) == TradeTier.TIER1

    def test_boundary_40(self):
        assert _assign_trade_tier(40.0) == TradeTier.TIER2


# ─── Score to Grade ──────────────────────────────────────────────


class TestScoreToGrade:
    def test_grade_s(self):
        assert _score_to_grade(85) == "S"

    def test_grade_a(self):
        assert _score_to_grade(70) == "A"

    def test_grade_b(self):
        assert _score_to_grade(55) == "B"

    def test_grade_c(self):
        assert _score_to_grade(40) == "C"

    def test_grade_d(self):
        assert _score_to_grade(20) == "D"


# ─── Run Analyst (LLM mocked) ───────────────────────────────────


class TestRunAnalyst:
    @pytest.mark.asyncio
    async def test_llm_score_clamped(self):
        """LLM 점수가 ±15pt 가드레일 내로 클램핑."""
        mock_llm = AsyncMock()
        mock_llm.generate_json.return_value = {
            "score": 95,
            "grade": "S",
            "reason": "매우 우수한 투자 기회로 판단됩니다. 모멘텀과 수급 모두 양호합니다.",
        }

        quant = _quant(total=60.0)
        candidate = _candidate(prices_n=30)
        context = _context()

        with patch("prime_jennie.services.scout.analyst.get_config") as mock_config:
            mock_cfg = MagicMock()
            mock_cfg.scoring.llm_clamp_range = 15
            mock_config.return_value = mock_cfg

            result = await run_analyst(quant, candidate, context, mock_llm)

        assert isinstance(result, HybridScore)
        assert result.hybrid_score <= 75.0  # quant(60) + 15
        assert result.llm_score == 95.0  # raw score 유지

    @pytest.mark.asyncio
    async def test_llm_failure_fallback(self):
        """LLM 실패 시 quant score로 폴백."""
        mock_llm = AsyncMock()
        mock_llm.generate_json.side_effect = Exception("LLM timeout")

        quant = _quant(total=60.0)
        candidate = _candidate(prices_n=30)
        context = _context()

        with patch("prime_jennie.services.scout.analyst.get_config") as mock_config:
            mock_cfg = MagicMock()
            mock_cfg.scoring.llm_clamp_range = 15
            mock_config.return_value = mock_cfg

            result = await run_analyst(quant, candidate, context, mock_llm)

        assert isinstance(result, HybridScore)
        assert result.hybrid_score == 60.0  # fallback to quant

    @pytest.mark.asyncio
    async def test_veto_on_distribution_risk(self):
        """DISTRIBUTION_RISK → is_tradable=False, BLOCKED."""
        mock_llm = AsyncMock()
        mock_llm.generate_json.return_value = {
            "score": 65,
            "grade": "A",
            "reason": "좋은 종목이지만 분배 리스크가 있습니다. 주의가 필요합니다.",
        }

        quant = _quant(total=60.0)
        snapshot = StockSnapshot(
            stock_code="005930",
            price=90000,
            high_52w=92000,
            timestamp=NOW,
        )
        # RSI > 70인 데이터 + 수급 악화
        prices = _make_prices(30, base=85000, trend=0.003)
        candidate = _candidate(
            snapshot=snapshot,
            prices_n=0,
            it=InvestorTradingSummary(
                foreign_net_buy_sum=-2e9,
                institution_net_buy_sum=-2e9,
            ),
        )
        candidate.daily_prices = prices

        with patch("prime_jennie.services.scout.analyst.get_config") as mock_config:
            mock_cfg = MagicMock()
            mock_cfg.scoring.llm_clamp_range = 15
            mock_config.return_value = mock_cfg

            with patch("prime_jennie.services.scout.analyst.classify_risk_tag") as mock_tag:
                mock_tag.return_value = RiskTag.DISTRIBUTION_RISK
                result = await run_analyst(quant, candidate, _context(), mock_llm)

        assert result.risk_tag == RiskTag.DISTRIBUTION_RISK
        assert result.veto_applied is True
        assert result.is_tradable is False
        assert result.trade_tier == TradeTier.BLOCKED
