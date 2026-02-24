"""Portfolio Guard 단위 테스트."""

import json
from unittest.mock import MagicMock

import pytest

from prime_jennie.domain.enums import MarketRegime, SectorGroup
from prime_jennie.domain.portfolio import Position
from prime_jennie.services.buyer.portfolio_guard import PortfolioGuard


@pytest.fixture(autouse=True)
def _clear_config_cache():
    from prime_jennie.domain.config import get_config

    get_config.cache_clear()
    yield
    get_config.cache_clear()


def _make_position(
    code: str = "005930",
    sector: SectorGroup = SectorGroup.SEMICONDUCTOR_IT,
) -> Position:
    return Position(
        stock_code=code,
        stock_name=f"Stock-{code}",
        quantity=100,
        average_buy_price=70000,
        total_buy_amount=7_000_000,
        sector_group=sector,
    )


class TestSectorStockCount:
    def test_within_limit(self):
        """섹터 한도 내."""
        guard = PortfolioGuard()
        positions = [_make_position("000001")]
        result = guard.check_sector_stock_count(SectorGroup.SEMICONDUCTOR_IT, positions)
        assert result.passed

    def test_at_limit(self):
        """섹터 한도 도달 → 차단."""
        guard = PortfolioGuard()
        positions = [_make_position(f"00000{i}", SectorGroup.SEMICONDUCTOR_IT) for i in range(3)]
        result = guard.check_sector_stock_count(SectorGroup.SEMICONDUCTOR_IT, positions)
        assert not result.passed
        assert "full" in result.reason

    def test_different_sector_ok(self):
        """다른 섹터는 무관."""
        guard = PortfolioGuard()
        positions = [_make_position(f"00000{i}", SectorGroup.SEMICONDUCTOR_IT) for i in range(3)]
        result = guard.check_sector_stock_count(SectorGroup.FINANCE, positions)
        assert result.passed

    def test_dynamic_cap_from_redis(self):
        """Redis 동적 cap 조회."""
        mock_redis = MagicMock()
        mock_redis.hget.return_value = json.dumps({"portfolio_cap": 5, "tier": "HOT"})

        guard = PortfolioGuard(redis_client=mock_redis)
        positions = [_make_position(f"00000{i}", SectorGroup.SEMICONDUCTOR_IT) for i in range(4)]
        result = guard.check_sector_stock_count(SectorGroup.SEMICONDUCTOR_IT, positions)
        # 동적 cap=5이므로 4개는 통과
        assert result.passed

    def test_redis_failure_fallback(self):
        """Redis 실패 시 고정 cap 사용."""
        mock_redis = MagicMock()
        mock_redis.hget.side_effect = Exception("Redis down")

        guard = PortfolioGuard(redis_client=mock_redis)
        positions = [_make_position(f"00000{i}", SectorGroup.SEMICONDUCTOR_IT) for i in range(3)]
        result = guard.check_sector_stock_count(SectorGroup.SEMICONDUCTOR_IT, positions)
        assert not result.passed


class TestCashFloor:
    def test_bull_cash_floor(self):
        """BULL 국면: 10% 하한선."""
        guard = PortfolioGuard()
        result = guard.check_cash_floor(
            buy_amount=2_000_000,
            available_cash=5_000_000,
            total_assets=50_000_000,
            regime=MarketRegime.BULL,
        )
        # cash_after = 3M, pct = 3/50 = 6% < 10% → FAIL
        assert not result.passed

    def test_strong_bull_cash_floor(self):
        """STRONG_BULL: 5% 하한선 → 여유 있음."""
        guard = PortfolioGuard()
        result = guard.check_cash_floor(
            buy_amount=2_000_000,
            available_cash=5_000_000,
            total_assets=50_000_000,
            regime=MarketRegime.STRONG_BULL,
        )
        # cash_after = 3M, pct = 6% > 5% → PASS
        assert result.passed

    def test_bear_cash_floor(self):
        """BEAR: 25% 하한선."""
        guard = PortfolioGuard()
        result = guard.check_cash_floor(
            buy_amount=1_000_000,
            available_cash=15_000_000,
            total_assets=50_000_000,
            regime=MarketRegime.BEAR,
        )
        # cash_after = 14M, pct = 28% > 25% → PASS
        assert result.passed

    def test_zero_assets(self):
        """자산 0 → 통과."""
        guard = PortfolioGuard()
        result = guard.check_cash_floor(0, 0, 0, MarketRegime.BULL)
        assert result.passed


class TestSectorValueConcentration:
    def test_within_limit(self):
        """섹터 금액 비중 한도 내."""
        guard = PortfolioGuard()
        positions = [_make_position("000001", SectorGroup.SEMICONDUCTOR_IT)]
        # 7M existing + 5M buy = 12M / 50M = 24% < 30%
        result = guard.check_sector_value_concentration(
            SectorGroup.SEMICONDUCTOR_IT, 5_000_000, 50_000_000, positions, MarketRegime.BULL
        )
        assert result.passed

    def test_exceeds_limit(self):
        """섹터 금액 비중 초과 → 차단."""
        guard = PortfolioGuard()
        # 3 positions * 7M = 21M + 5M buy = 26M / 50M = 52% > 30%
        positions = [_make_position(f"00000{i}", SectorGroup.SEMICONDUCTOR_IT) for i in range(3)]
        result = guard.check_sector_value_concentration(
            SectorGroup.SEMICONDUCTOR_IT, 5_000_000, 50_000_000, positions, MarketRegime.BULL
        )
        assert not result.passed
        assert "sector_value" in result.check_name

    def test_strong_bull_relaxed(self):
        """STRONG_BULL: 50% 완화."""
        guard = PortfolioGuard()
        # 3 * 7M = 21M + 2M = 23M / 50M = 46% < 50%
        positions = [_make_position(f"00000{i}", SectorGroup.SEMICONDUCTOR_IT) for i in range(3)]
        result = guard.check_sector_value_concentration(
            SectorGroup.SEMICONDUCTOR_IT, 2_000_000, 50_000_000, positions, MarketRegime.STRONG_BULL
        )
        assert result.passed

    def test_zero_assets(self):
        """자산 0 → 통과."""
        guard = PortfolioGuard()
        result = guard.check_sector_value_concentration(SectorGroup.SEMICONDUCTOR_IT, 0, 0, [], MarketRegime.BULL)
        assert result.passed


class TestStockValueConcentration:
    def test_within_limit(self):
        """종목 금액 비중 한도 내."""
        guard = PortfolioGuard()
        # 6M / 50M = 12% < 15%
        result = guard.check_stock_value_concentration(6_000_000, 50_000_000, MarketRegime.BULL)
        assert result.passed

    def test_exceeds_limit(self):
        """종목 금액 비중 초과 → 차단."""
        guard = PortfolioGuard()
        # 8M / 50M = 16% > 15%
        result = guard.check_stock_value_concentration(8_000_000, 50_000_000, MarketRegime.BULL)
        assert not result.passed
        assert "stock_value" in result.check_name

    def test_strong_bull_relaxed(self):
        """STRONG_BULL: 25% 완화."""
        guard = PortfolioGuard()
        # 10M / 50M = 20% < 25%
        result = guard.check_stock_value_concentration(10_000_000, 50_000_000, MarketRegime.STRONG_BULL)
        assert result.passed

    def test_zero_assets(self):
        """자산 0 → 통과."""
        guard = PortfolioGuard()
        result = guard.check_stock_value_concentration(0, 0, MarketRegime.BULL)
        assert result.passed


class TestCheckAll:
    def test_all_pass(self):
        """모든 체크 통과."""
        guard = PortfolioGuard()
        positions = [_make_position("000001", SectorGroup.FINANCE)]
        result = guard.check_all(
            sector_group=SectorGroup.SEMICONDUCTOR_IT,
            buy_amount=2_000_000,
            available_cash=10_000_000,
            total_assets=50_000_000,
            positions=positions,
            regime=MarketRegime.BULL,
        )
        assert result.passed

    def test_sector_fail_blocks(self):
        """섹터 제한 차단."""
        guard = PortfolioGuard()
        positions = [_make_position(f"00000{i}", SectorGroup.SEMICONDUCTOR_IT) for i in range(3)]
        result = guard.check_all(
            sector_group=SectorGroup.SEMICONDUCTOR_IT,
            buy_amount=2_000_000,
            available_cash=10_000_000,
            total_assets=50_000_000,
            positions=positions,
            regime=MarketRegime.BULL,
        )
        assert not result.passed
        assert "sector" in result.check_name
