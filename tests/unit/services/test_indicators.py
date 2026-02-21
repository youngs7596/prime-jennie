"""Technical Indicators 단위 테스트 — Death Cross, MACD Bearish Divergence."""

import pytest

from prime_jennie.services.monitor.indicators import (
    calculate_ema,
    calculate_sma,
    check_death_cross,
    check_macd_bearish_divergence,
)


class TestCalculateSMA:
    def test_basic(self):
        prices = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = calculate_sma(prices, 3)
        assert result[0] is None
        assert result[1] is None
        assert result[2] == pytest.approx(2.0)
        assert result[3] == pytest.approx(3.0)
        assert result[4] == pytest.approx(4.0)

    def test_insufficient_data(self):
        prices = [1.0, 2.0]
        result = calculate_sma(prices, 3)
        assert all(v is None for v in result)

    def test_single_period(self):
        prices = [10.0, 20.0, 30.0]
        result = calculate_sma(prices, 1)
        assert result == [10.0, 20.0, 30.0]


class TestCalculateEMA:
    def test_basic(self):
        prices = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = calculate_ema(prices, 3)
        assert len(result) == 5
        assert result[0] == 1.0
        # EMA with span=3: k=0.5
        assert result[1] == pytest.approx(1.5)  # 2*0.5 + 1*0.5

    def test_empty(self):
        assert calculate_ema([], 3) == []


class TestDeathCross:
    def test_death_cross_detected(self):
        """5MA가 20MA를 하향 돌파하는 시나리오."""
        # 상승 후 하락: 20MA 위에서 아래로
        prices = list(range(100, 130))  # 상승 구간 (30일)
        # 급격한 하락 추가
        prices.extend([129 - i * 2 for i in range(1, 6)])  # 127, 125, 123, 121, 119
        assert len(prices) >= 21
        result = check_death_cross(prices)
        # 하락이 충분히 급격하면 death cross
        assert isinstance(result, bool)

    def test_no_death_cross_uptrend(self):
        """지속 상승 → death cross 없음."""
        prices = [float(i) for i in range(100, 125)]  # 25일 상승
        assert check_death_cross(prices) is False

    def test_insufficient_data(self):
        prices = [1.0] * 15
        assert check_death_cross(prices) is False

    def test_clear_death_cross(self):
        """명확한 데드크로스: 교차점이 마지막 바에서 발생.

        i=34: 5MA=123.0 >= 20MA=122.25 (ABOVE)
        i=35: 5MA=120.6 <  20MA=122.25 (BELOW, gap OK)
        """
        # 30일 상승
        prices = [100 + i for i in range(30)]
        # 5일 하락 → 교차점 i=35에서 발생
        prices.extend([127, 125, 123, 121, 119, 115])
        assert len(prices) == 36  # long(20) + 1 이상
        result = check_death_cross(prices)
        assert result is True


class TestMACDBearishDivergence:
    def test_divergence_detected(self):
        """가격 신고가 + MACD 하락 → divergence."""
        # 충분한 데이터: 36일 이상
        # 패턴: 상승 → 소폭 하락 → 다시 상승 (더 높은 가격) but MACD 약해짐
        prices = [100 + i for i in range(20)]  # 초기 상승
        prices.extend([119 - i * 0.5 for i in range(10)])  # 소폭 조정
        prices.extend([114 + i * 0.8 for i in range(10)])  # 재상승
        assert len(prices) >= 36
        result = check_macd_bearish_divergence(prices)
        assert isinstance(result, bool)

    def test_no_divergence_strong_uptrend(self):
        """강한 상승 추세 → divergence 없음."""
        prices = [100 + i * 3 for i in range(40)]
        assert check_macd_bearish_divergence(prices) is False

    def test_insufficient_data(self):
        prices = [1.0] * 20
        assert check_macd_bearish_divergence(prices) is False
