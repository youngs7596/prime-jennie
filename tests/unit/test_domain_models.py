"""Domain model unit tests — 계약 검증."""

import datetime

import pytest
from pydantic import ValidationError

from prime_jennie.domain import (
    BuySignal,
    HotWatchlist,
    HybridScore,
    LLMAnalysis,
    MacroInsight,
    MarketRegime,
    OrderRequest,
    OrderType,
    PortfolioState,
    Position,
    QuantScore,
    RiskTag,
    SectorBudget,
    SectorBudgetEntry,
    SectorGroup,
    SectorTier,
    Sentiment,
    SignalType,
    TradeTier,
    TradingContext,
    WatchlistEntry,
)

# ─── QuantScore ──────────────────────────────────────────────────


class TestQuantScore:
    def test_valid_score(self):
        qs = QuantScore(
            stock_code="005930",
            stock_name="삼성전자",
            total_score=60.0,
            momentum_score=12.0,
            quality_score=10.0,
            value_score=15.0,
            technical_score=8.0,
            news_score=7.0,
            supply_demand_score=8.0,
        )
        assert qs.total_score == 60.0
        assert qs.is_valid is True

    def test_subscore_mismatch_rejected(self):
        with pytest.raises(ValidationError, match="total_score"):
            QuantScore(
                stock_code="005930",
                stock_name="Test",
                total_score=99.0,
                momentum_score=1.0,
                quality_score=1.0,
                value_score=1.0,
                technical_score=1.0,
                news_score=1.0,
                supply_demand_score=1.0,
            )

    def test_small_rounding_tolerance(self):
        """1.5 이내 오차는 허용."""
        qs = QuantScore(
            stock_code="005930",
            stock_name="Test",
            total_score=61.0,
            momentum_score=12.0,
            quality_score=10.0,
            value_score=15.0,
            technical_score=8.0,
            news_score=7.0,
            supply_demand_score=8.0,
        )
        assert qs.total_score == 61.0

    def test_invalid_stock_code(self):
        with pytest.raises(ValidationError):
            QuantScore(
                stock_code="ABC",
                stock_name="Test",
                total_score=0.0,
            )

    def test_score_out_of_range(self):
        with pytest.raises(ValidationError):
            QuantScore(
                stock_code="005930",
                stock_name="Test",
                total_score=150.0,
            )


# ─── HybridScore ────────────────────────────────────────────────


class TestHybridScore:
    @pytest.fixture
    def now(self):
        return datetime.datetime.now()

    def test_valid_hybrid(self, now):
        hs = HybridScore(
            stock_code="005930",
            stock_name="삼성전자",
            quant_score=60.0,
            llm_score=65.0,
            hybrid_score=65.0,
            risk_tag=RiskTag.NEUTRAL,
            trade_tier=TradeTier.TIER1,
            is_tradable=True,
            scored_at=now,
        )
        assert hs.is_tradable is True

    def test_blocked_must_not_be_tradable(self, now):
        with pytest.raises(ValidationError, match="BLOCKED"):
            HybridScore(
                stock_code="005930",
                stock_name="Test",
                quant_score=30.0,
                llm_score=30.0,
                hybrid_score=30.0,
                risk_tag=RiskTag.CAUTION,
                trade_tier=TradeTier.BLOCKED,
                is_tradable=True,
                scored_at=now,
            )

    def test_distribution_risk_requires_veto(self, now):
        with pytest.raises(ValidationError, match="DISTRIBUTION_RISK"):
            HybridScore(
                stock_code="005930",
                stock_name="Test",
                quant_score=30.0,
                llm_score=30.0,
                hybrid_score=30.0,
                risk_tag=RiskTag.DISTRIBUTION_RISK,
                trade_tier=TradeTier.BLOCKED,
                is_tradable=False,
                veto_applied=False,
                scored_at=now,
            )

    def test_valid_blocked_with_veto(self, now):
        hs = HybridScore(
            stock_code="005930",
            stock_name="Test",
            quant_score=30.0,
            llm_score=30.0,
            hybrid_score=30.0,
            risk_tag=RiskTag.DISTRIBUTION_RISK,
            trade_tier=TradeTier.BLOCKED,
            is_tradable=False,
            veto_applied=True,
            scored_at=now,
        )
        assert hs.veto_applied is True


# ─── LLMAnalysis ────────────────────────────────────────────────


class TestLLMAnalysis:
    def test_reason_too_short(self):
        with pytest.raises(ValidationError, match="reason"):
            LLMAnalysis(
                stock_code="005930",
                raw_score=70.0,
                clamped_score=65.0,
                grade="B",
                reason="Short",
                scored_at=datetime.datetime.now(),
            )

    def test_valid_analysis(self):
        la = LLMAnalysis(
            stock_code="005930",
            raw_score=70.0,
            clamped_score=65.0,
            grade="B",
            reason="실적 개선 추세와 반도체 업황 회복 기대감 반영",
            scored_at=datetime.datetime.now(),
        )
        assert la.grade == "B"


# ─── TradingContext ──────────────────────────────────────────────


class TestTradingContext:
    def test_default_is_conservative(self):
        tc = TradingContext.default()
        assert tc.market_regime == MarketRegime.SIDEWAYS
        assert tc.position_multiplier == 0.8
        assert tc.stop_loss_multiplier == 1.2

    def test_custom_context(self):
        tc = TradingContext(
            date=datetime.date.today(),
            market_regime=MarketRegime.BULL,
            position_multiplier=1.2,
            favor_sectors=[SectorGroup.SEMICONDUCTOR_IT],
            avoid_sectors=[SectorGroup.CONSTRUCTION],
        )
        assert len(tc.favor_sectors) == 1
        assert len(tc.avoid_sectors) == 1


# ─── HotWatchlist ────────────────────────────────────────────────


class TestHotWatchlist:
    @pytest.fixture
    def watchlist(self):
        return HotWatchlist(
            generated_at=datetime.datetime.now(),
            market_regime=MarketRegime.BULL,
            stocks=[
                WatchlistEntry(
                    stock_code="005930",
                    stock_name="삼성전자",
                    llm_score=72.0,
                    hybrid_score=68.0,
                    rank=1,
                    is_tradable=True,
                    trade_tier=TradeTier.TIER1,
                ),
                WatchlistEntry(
                    stock_code="000660",
                    stock_name="SK하이닉스",
                    llm_score=45.0,
                    hybrid_score=43.0,
                    rank=2,
                    is_tradable=False,
                    trade_tier=TradeTier.BLOCKED,
                    veto_applied=True,
                    risk_tag=RiskTag.DISTRIBUTION_RISK,
                ),
            ],
            version="v20260219",
        )

    def test_stock_codes(self, watchlist):
        assert watchlist.stock_codes == ["005930", "000660"]

    def test_tradable_stocks(self, watchlist):
        assert len(watchlist.tradable_stocks) == 1
        assert watchlist.tradable_stocks[0].stock_code == "005930"

    def test_get_stock(self, watchlist):
        s = watchlist.get_stock("005930")
        assert s is not None
        assert s.stock_name == "삼성전자"

    def test_get_stock_not_found(self, watchlist):
        assert watchlist.get_stock("999999") is None


# ─── SectorBudget ────────────────────────────────────────────────


class TestSectorBudget:
    def test_get_cap_existing(self):
        sb = SectorBudget(
            entries={
                SectorGroup.SEMICONDUCTOR_IT: SectorBudgetEntry(
                    sector_group=SectorGroup.SEMICONDUCTOR_IT,
                    tier=SectorTier.HOT,
                    watchlist_cap=5,
                    portfolio_cap=5,
                    effective_cap=4,
                    held_count=1,
                ),
            },
            generated_at="2026-02-19T10:00:00",
        )
        assert sb.get_cap(SectorGroup.SEMICONDUCTOR_IT) == 4

    def test_get_cap_default(self):
        sb = SectorBudget(entries={}, generated_at="2026-02-19T10:00:00")
        assert sb.get_cap(SectorGroup.ETC) == 3  # WARM default

    def test_is_available(self):
        sb = SectorBudget(
            entries={
                SectorGroup.CONSTRUCTION: SectorBudgetEntry(
                    sector_group=SectorGroup.CONSTRUCTION,
                    tier=SectorTier.COOL,
                    watchlist_cap=2,
                    portfolio_cap=2,
                    effective_cap=0,
                    held_count=2,
                ),
            },
            generated_at="2026-02-19T10:00:00",
        )
        assert sb.is_available(SectorGroup.CONSTRUCTION) is False


# ─── PortfolioState ──────────────────────────────────────────────


class TestPortfolioState:
    def test_cash_ratio_no_assets(self):
        ps = PortfolioState(
            positions=[],
            cash_balance=0,
            total_asset=0,
            stock_eval_amount=0,
            position_count=0,
            timestamp=datetime.datetime.now(),
        )
        assert ps.cash_ratio == 1.0

    def test_cash_ratio_normal(self):
        ps = PortfolioState(
            positions=[],
            cash_balance=3_000_000,
            total_asset=10_000_000,
            stock_eval_amount=7_000_000,
            position_count=3,
            timestamp=datetime.datetime.now(),
        )
        assert abs(ps.cash_ratio - 0.3) < 0.001

    def test_sector_distribution(self):
        ps = PortfolioState(
            positions=[
                Position(
                    stock_code="005930",
                    stock_name="삼성전자",
                    quantity=10,
                    average_buy_price=70000,
                    total_buy_amount=700000,
                    sector_group=SectorGroup.SEMICONDUCTOR_IT,
                ),
                Position(
                    stock_code="000660",
                    stock_name="SK하이닉스",
                    quantity=5,
                    average_buy_price=150000,
                    total_buy_amount=750000,
                    sector_group=SectorGroup.SEMICONDUCTOR_IT,
                ),
                Position(
                    stock_code="068270",
                    stock_name="셀트리온",
                    quantity=3,
                    average_buy_price=200000,
                    total_buy_amount=600000,
                    sector_group=SectorGroup.BIO_HEALTH,
                ),
            ],
            cash_balance=1_000_000,
            total_asset=3_050_000,
            stock_eval_amount=2_050_000,
            position_count=3,
            timestamp=datetime.datetime.now(),
        )
        dist = ps.sector_distribution
        assert dist[SectorGroup.SEMICONDUCTOR_IT] == 2
        assert dist[SectorGroup.BIO_HEALTH] == 1


# ─── BuySignal ───────────────────────────────────────────────────


class TestBuySignal:
    def test_valid_signal(self):
        bs = BuySignal(
            stock_code="005930",
            stock_name="삼성전자",
            signal_type=SignalType.MOMENTUM,
            signal_price=78000,
            llm_score=72.0,
            hybrid_score=68.0,
            trade_tier=TradeTier.TIER1,
            market_regime=MarketRegime.BULL,
            timestamp=datetime.datetime.now(),
        )
        assert bs.source == "scanner"
        assert bs.position_multiplier == 1.0

    def test_limit_order_fields(self):
        req = OrderRequest(
            stock_code="005930",
            quantity=10,
            order_type=OrderType.LIMIT,
            price=78000,
        )
        assert req.price == 78000


# ─── MacroInsight ────────────────────────────────────────────────


class TestMacroInsight:
    def test_valid_insight(self):
        mi = MacroInsight(
            insight_date=datetime.date.today(),
            sentiment=Sentiment.NEUTRAL_TO_BULLISH,
            sentiment_score=65.0,
            regime_hint="BULL",
            sectors_to_favor=[SectorGroup.SEMICONDUCTOR_IT],
            sectors_to_avoid=[SectorGroup.CONSTRUCTION],
            position_size_pct=110,
        )
        assert mi.political_risk_level == "low"
        assert len(mi.sectors_to_favor) == 1

    def test_position_size_bounds(self):
        with pytest.raises(ValidationError):
            MacroInsight(
                insight_date=datetime.date.today(),
                sentiment=Sentiment.BULLISH,
                sentiment_score=80.0,
                regime_hint="BULL",
                position_size_pct=200,  # max 130
            )
