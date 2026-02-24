"""Correlation Check 단위 테스트."""

from prime_jennie.domain.enums import SectorGroup
from prime_jennie.domain.portfolio import Position
from prime_jennie.services.buyer.correlation import (
    calculate_correlation,
    check_portfolio_correlation,
)


def _make_position(code: str = "005930") -> Position:
    return Position(
        stock_code=code,
        stock_name=f"Stock-{code}",
        quantity=100,
        average_buy_price=70000,
        total_buy_amount=7_000_000,
        sector_group=SectorGroup.SEMICONDUCTOR_IT,
    )


class TestCalculateCorrelation:
    def test_perfect_positive(self):
        """동일 시계열 → 상관계수 ~1.0."""
        prices = [100 + i for i in range(30)]
        corr = calculate_correlation(prices, prices)
        assert corr is not None
        assert corr > 0.99

    def test_inverse_correlation(self):
        """역상관 시계열 → 음의 상관계수."""
        import math

        # 사인파 vs 역사인파로 확실한 역상관 생성
        prices_a = [100 + 10 * math.sin(i * 0.3) for i in range(50)]
        prices_b = [100 - 10 * math.sin(i * 0.3) for i in range(50)]
        corr = calculate_correlation(prices_a, prices_b)
        assert corr is not None
        assert corr < -0.9

    def test_insufficient_data(self):
        """데이터 부족 → None."""
        corr = calculate_correlation([100, 101], [200, 201])
        assert corr is None

    def test_different_lengths(self):
        """길이 다른 시계열 → 최근 기준 맞춤."""
        prices_a = [100 + i * 0.5 for i in range(40)]
        prices_b = [200 + i * 0.3 for i in range(25)]
        corr = calculate_correlation(prices_a, prices_b)
        assert corr is not None
        assert corr > 0.5

    def test_constant_prices(self):
        """변동 없는 가격 → None (std=0)."""
        prices_a = [100.0] * 30
        prices_b = [200.0] * 30
        corr = calculate_correlation(prices_a, prices_b)
        # log returns all zero → std=0 → NaN correlation
        assert corr is None


class TestCheckPortfolioCorrelation:
    def test_no_positions_passes(self):
        """보유 종목 없으면 통과."""
        passed, max_corr, msg = check_portfolio_correlation(
            "005930", [100 + i for i in range(30)], [], lambda c: [], 0.85
        )
        assert passed
        assert max_corr == 0.0

    def test_high_correlation_blocks(self):
        """고상관 종목 → 차단."""
        prices = [100 + i for i in range(30)]
        positions = [_make_position("000660")]

        passed, max_corr, msg = check_portfolio_correlation(
            "005930",
            prices,
            positions,
            lambda c: prices,  # 동일 가격 → corr≈1.0
            0.85,
        )
        assert not passed
        assert max_corr > 0.85
        assert "000660" in msg

    def test_low_correlation_passes(self):
        """저상관 종목 → 통과."""
        import random

        random.seed(42)
        prices_a = [100 + i for i in range(30)]
        prices_b = [200 + random.uniform(-5, 5) for _ in range(30)]
        positions = [_make_position("000660")]

        passed, max_corr, msg = check_portfolio_correlation(
            "005930",
            prices_a,
            positions,
            lambda c: prices_b,
            0.85,
        )
        assert passed

    def test_price_lookup_failure_skips(self):
        """가격 조회 실패 시 해당 종목 스킵."""
        prices = [100 + i for i in range(30)]
        positions = [_make_position("000660")]

        def failing_lookup(code):
            raise Exception("API error")

        passed, max_corr, msg = check_portfolio_correlation("005930", prices, positions, failing_lookup, 0.85)
        assert passed
        assert max_corr == 0.0
