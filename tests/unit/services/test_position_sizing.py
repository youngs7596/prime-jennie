"""Position Sizing 단위 테스트."""

import pytest

from prime_jennie.domain.enums import SectorGroup, TradeTier
from prime_jennie.domain.trading import PositionSizingRequest
from prime_jennie.services.buyer.position_sizing import (
    MAX_QUANTITY,
    PORTFOLIO_HEAT_LIMIT,
    calculate_atr,
    calculate_position_size,
    check_portfolio_heat,
    clamp_atr,
    get_dynamic_max_position_pct,
    get_sector_risk_multiplier,
    get_stale_multiplier,
    get_tier_multiplier,
)


@pytest.fixture(autouse=True)
def _clear_config_cache():
    from prime_jennie.domain.config import get_config
    get_config.cache_clear()
    yield
    get_config.cache_clear()


def _make_request(**overrides) -> PositionSizingRequest:
    defaults = {
        "stock_code": "005930",
        "stock_price": 70000,
        "atr": 1400.0,
        "available_cash": 10_000_000,
        "portfolio_value": 20_000_000,
        "llm_score": 65.0,
        "trade_tier": TradeTier.TIER1,
    }
    defaults.update(overrides)
    return PositionSizingRequest(**defaults)


class TestDynamicMaxPositionPct:
    def test_default_12(self):
        assert get_dynamic_max_position_pct(70.0) == 12.0

    def test_a_plus_18(self):
        assert get_dynamic_max_position_pct(80.0) == 18.0

    def test_boundary(self):
        assert get_dynamic_max_position_pct(79.9) == 12.0


class TestSectorRiskMultiplier:
    def test_no_held_sectors(self):
        assert get_sector_risk_multiplier(SectorGroup.SEMICONDUCTOR_IT, []) == 1.0

    def test_different_sector(self):
        assert get_sector_risk_multiplier(
            SectorGroup.SEMICONDUCTOR_IT, [SectorGroup.FINANCE]
        ) == 1.0

    def test_same_sector_discount(self):
        assert get_sector_risk_multiplier(
            SectorGroup.SEMICONDUCTOR_IT, [SectorGroup.SEMICONDUCTOR_IT]
        ) == 0.7

    def test_none_sector(self):
        assert get_sector_risk_multiplier(None, [SectorGroup.FINANCE]) == 1.0


class TestPortfolioHeat:
    def test_within_limit(self):
        assert check_portfolio_heat(3.0, 1.5) is True

    def test_exceeds_limit(self):
        assert check_portfolio_heat(4.0, 1.5) is False

    def test_at_limit(self):
        assert check_portfolio_heat(4.0, 1.0) is True


class TestTierMultiplier:
    def test_tier1(self):
        assert get_tier_multiplier(TradeTier.TIER1) == 1.0

    def test_tier2(self):
        assert get_tier_multiplier(TradeTier.TIER2) == 0.5

    def test_blocked(self):
        assert get_tier_multiplier(TradeTier.BLOCKED) == 0.0


class TestStaleMultiplier:
    def test_fresh(self):
        assert get_stale_multiplier(0) == 1.0

    def test_1_day(self):
        assert get_stale_multiplier(1) == 1.0

    def test_2_days(self):
        assert get_stale_multiplier(2) == 0.5

    def test_3_plus_days(self):
        assert get_stale_multiplier(3) == 0.3
        assert get_stale_multiplier(5) == 0.3


class TestCalculateATR:
    def test_basic_atr(self):
        prices = [
            {"high": 110, "low": 90, "close": 100},
            {"high": 115, "low": 95, "close": 105},
            {"high": 120, "low": 100, "close": 110},
        ]
        atr = calculate_atr(prices, period=14)
        assert atr > 0

    def test_single_price(self):
        prices = [{"high": 100, "low": 90, "close": 95}]
        assert calculate_atr(prices) == 0.0

    def test_empty_prices(self):
        assert calculate_atr([]) == 0.0


class TestClampATR:
    def test_normal_range(self):
        # 2% of 70000 = 1400 (within 1-5%)
        result = clamp_atr(1400, 70000)
        assert result == 1400

    def test_too_low(self):
        # 0.5% of 70000 = 350, min = 1% = 700
        result = clamp_atr(350, 70000)
        assert result == 700

    def test_too_high(self):
        # 10% of 70000 = 7000, max = 5% = 3500
        result = clamp_atr(7000, 70000)
        assert result == 3500

    def test_zero_atr(self):
        result = clamp_atr(0, 70000)
        assert result == 70000 * 0.02

    def test_zero_price(self):
        result = clamp_atr(100, 0)
        assert result == 0


class TestCalculatePositionSize:
    def test_basic_sizing(self):
        """기본 포지션 사이징."""
        request = _make_request()
        result = calculate_position_size(request)
        assert result.quantity > 0
        assert result.actual_weight_pct > 0

    def test_tier2_discount(self):
        """TIER2 → 0.5x."""
        req_t1 = _make_request(trade_tier=TradeTier.TIER1)
        req_t2 = _make_request(trade_tier=TradeTier.TIER2)

        result_t1 = calculate_position_size(req_t1)
        result_t2 = calculate_position_size(req_t2)

        assert result_t2.quantity <= result_t1.quantity
        assert result_t2.applied_multipliers["tier"] == 0.5

    def test_stale_discount(self):
        """Stale score 감산."""
        req_fresh = _make_request(stale_days=0)
        req_stale = _make_request(stale_days=3)

        result_fresh = calculate_position_size(req_fresh)
        result_stale = calculate_position_size(req_stale)

        assert result_stale.quantity <= result_fresh.quantity

    def test_no_cash(self):
        """현금 없으면 0수량."""
        request = _make_request(available_cash=0, portfolio_value=0)
        result = calculate_position_size(request)
        assert result.quantity == 0

    def test_zero_atr(self):
        """ATR 0이면 0수량."""
        request = _make_request(atr=0)
        result = calculate_position_size(request)
        assert result.quantity == 0

    def test_blocked_tier_zero(self):
        """BLOCKED 티어 → 0수량."""
        request = _make_request(trade_tier=TradeTier.BLOCKED)
        result = calculate_position_size(request)
        assert result.quantity == 0

    def test_sector_discount_applied(self):
        """동일 섹터 보유 시 감산."""
        request = _make_request(
            sector_group=SectorGroup.SEMICONDUCTOR_IT,
            held_sector_groups=[SectorGroup.SEMICONDUCTOR_IT],
        )
        result = calculate_position_size(request)
        assert result.applied_multipliers["sector"] == 0.7

    def test_max_quantity_capped(self):
        """최대 수량 제한."""
        request = _make_request(
            stock_price=10,  # 매우 저가
            available_cash=100_000_000,
            portfolio_value=0,
            atr=0.2,
        )
        result = calculate_position_size(request)
        assert result.quantity <= MAX_QUANTITY
