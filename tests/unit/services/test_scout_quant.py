"""Scout Quant Scorer v2 단위 테스트."""

from datetime import date, datetime, timezone

import pytest

from prime_jennie.domain.enums import SectorGroup
from prime_jennie.domain.scoring import QuantScore
from prime_jennie.domain.stock import DailyPrice, StockMaster, StockSnapshot
from prime_jennie.services.scout.enrichment import (
    EnrichedCandidate,
    FinancialTrend,
    InvestorTradingSummary,
)
from prime_jennie.services.scout.quant import (
    V2_NEUTRAL,
    _compute_rsi,
    _linear_map,
    _momentum_score,
    _news_score,
    _quality_score,
    _supply_demand_score,
    _technical_score,
    _value_score,
    score_candidate,
)


# ─── Fixtures ────────────────────────────────────────────────────


def _make_master(code: str = "005930", name: str = "삼성전자") -> StockMaster:
    return StockMaster(
        stock_code=code,
        stock_name=name,
        market="KOSPI",
        sector_group=SectorGroup.SEMICONDUCTOR_IT,
    )


def _make_prices(n: int = 150, base: int = 70000, trend: float = 0.001) -> list[DailyPrice]:
    """n일치 일봉 생성 (상승 추세)."""
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


def _make_candidate(
    prices: list[DailyPrice] | None = None,
    snapshot: StockSnapshot | None = None,
    ft: FinancialTrend | None = None,
    it: InvestorTradingSummary | None = None,
    news_avg: float | None = None,
) -> EnrichedCandidate:
    return EnrichedCandidate(
        master=_make_master(),
        snapshot=snapshot,
        daily_prices=prices or [],
        financial_trend=ft,
        investor_trading=it,
        news_sentiment_avg=news_avg,
    )


# ─── Total Score ─────────────────────────────────────────────────


class TestScoreCandidate:
    def test_valid_score_has_six_subscores(self):
        candidate = _make_candidate(
            prices=_make_prices(150),
            ft=FinancialTrend(per=12.0, pbr=1.5, roe=12.0),
            it=InvestorTradingSummary(foreign_net_buy_sum=1e9, institution_net_buy_sum=5e8),
            news_avg=60.0,
        )
        result = score_candidate(candidate)

        assert isinstance(result, QuantScore)
        assert 0 <= result.total_score <= 100
        assert result.momentum_score >= 0
        assert result.quality_score >= 0
        assert result.value_score >= 0
        assert result.technical_score >= 0
        assert result.news_score >= 0
        assert result.supply_demand_score >= 0
        assert result.is_valid is True

    def test_total_equals_sum_of_subscores(self):
        candidate = _make_candidate(
            prices=_make_prices(150),
            ft=FinancialTrend(per=10.0, pbr=0.8, roe=15.0),
        )
        result = score_candidate(candidate)

        expected = (
            result.momentum_score
            + result.quality_score
            + result.value_score
            + result.technical_score
            + result.news_score
            + result.supply_demand_score
        )
        assert abs(result.total_score - expected) <= 1.5

    def test_insufficient_data_returns_neutral(self):
        candidate = _make_candidate(prices=_make_prices(5))
        result = score_candidate(candidate)

        assert result.is_valid is False
        assert result.total_score == 50.0

    def test_score_bounded_0_100(self):
        candidate = _make_candidate(
            prices=_make_prices(150, trend=0.005),
            ft=FinancialTrend(per=5.0, pbr=0.5, roe=20.0),
            it=InvestorTradingSummary(
                foreign_net_buy_sum=10e9,
                institution_net_buy_sum=10e9,
                foreign_ratio_trend=2.0,
            ),
            news_avg=95.0,
            snapshot=StockSnapshot(
                stock_code="005930",
                price=80000,
                high_52w=90000,
                low_52w=50000,
                timestamp=datetime.now(timezone.utc),
            ),
        )
        result = score_candidate(candidate)
        assert 0 <= result.total_score <= 100


# ─── Sub-factors ─────────────────────────────────────────────────


class TestMomentumScore:
    def test_returns_neutral_for_short_data(self):
        prices = _make_prices(10)
        result = _momentum_score(prices, None)
        assert result == V2_NEUTRAL["momentum"]

    def test_uptrend_gets_higher_score(self):
        prices = _make_prices(150, trend=0.003)
        result = _momentum_score(prices, None)
        assert result > 5.0  # 상승 추세면 기본값(V2_NEUTRAL=10)의 절반 이상


class TestQualityScore:
    def test_high_roe_high_score(self):
        candidate = _make_candidate(ft=FinancialTrend(roe=20.0, pbr=1.0, per=10.0))
        result = _quality_score(candidate)
        assert result >= 15.0

    def test_negative_roe_low_score(self):
        candidate = _make_candidate(ft=FinancialTrend(roe=-5.0, pbr=3.0, per=50.0))
        result = _quality_score(candidate)
        assert result < 5.0

    def test_no_data_returns_neutral(self):
        candidate = _make_candidate()
        result = _quality_score(candidate)
        assert result == V2_NEUTRAL["quality"]


class TestValueScore:
    def test_low_per_high_score(self):
        candidate = _make_candidate(ft=FinancialTrend(per=6.0, pbr=0.5))
        result = _value_score(candidate)
        assert result >= 15.0

    def test_high_per_low_score(self):
        candidate = _make_candidate(ft=FinancialTrend(per=100.0, pbr=5.0))
        result = _value_score(candidate)
        assert result < 5.0


class TestTechnicalScore:
    def test_returns_neutral_for_short_data(self):
        prices = _make_prices(5)
        result = _technical_score(prices)
        assert result == V2_NEUTRAL["technical"]

    def test_bullish_alignment_higher_score(self):
        # 정배열 데이터: 최근 가격이 MA5 > MA20
        prices = _make_prices(30, trend=0.005)
        result = _technical_score(prices)
        assert result > 5.0


class TestNewsScore:
    def test_positive_sentiment_high_score(self):
        candidate = _make_candidate(news_avg=80.0)
        result = _news_score(candidate)
        assert result >= 8.0

    def test_negative_sentiment_low_score(self):
        candidate = _make_candidate(news_avg=20.0)
        result = _news_score(candidate)
        assert result <= 2.0

    def test_no_data_returns_neutral(self):
        candidate = _make_candidate()
        result = _news_score(candidate)
        assert result == V2_NEUTRAL["news"]


class TestSupplyDemandScore:
    def test_strong_foreign_buying_high_score(self):
        candidate = _make_candidate(
            it=InvestorTradingSummary(
                foreign_net_buy_sum=5e9,
                institution_net_buy_sum=3e9,
                foreign_ratio_trend=1.5,
            )
        )
        result = _supply_demand_score(candidate)
        assert result >= 15.0

    def test_strong_selling_low_score(self):
        candidate = _make_candidate(
            it=InvestorTradingSummary(
                foreign_net_buy_sum=-5e9,
                institution_net_buy_sum=-3e9,
                foreign_ratio_trend=-1.0,
            )
        )
        result = _supply_demand_score(candidate)
        assert result < 8.0


# ─── Helpers ─────────────────────────────────────────────────────


class TestComputeRSI:
    def test_uptrend_rsi_above_50(self):
        closes = [100 + i for i in range(30)]
        rsi = _compute_rsi(closes)
        assert rsi is not None
        assert rsi > 50

    def test_downtrend_rsi_below_50(self):
        closes = [200 - i for i in range(30)]
        rsi = _compute_rsi(closes)
        assert rsi is not None
        assert rsi < 50

    def test_insufficient_data_returns_none(self):
        assert _compute_rsi([100, 101, 102]) is None


class TestLinearMap:
    def test_midpoint(self):
        assert _linear_map(50, 0, 100, 0, 10) == 5.0

    def test_clamped_below(self):
        assert _linear_map(-10, 0, 100, 0, 10) == 0.0

    def test_clamped_above(self):
        assert _linear_map(200, 0, 100, 0, 10) == 10.0
