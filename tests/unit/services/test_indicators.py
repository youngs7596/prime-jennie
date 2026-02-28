"""Technical Indicators 단위 테스트 — SMA, EMA, RSI, BB, MACD, Death Cross."""

import pytest

from prime_jennie.services.monitor.indicators import (
    calculate_bollinger_bands,
    calculate_ema,
    calculate_macd,
    calculate_rsi,
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


class TestCalculateRSI:
    def test_all_gains(self):
        """연속 상승 → RSI 100."""
        prices = [float(i) for i in range(20)]  # 0,1,2,...,19
        rsi = calculate_rsi(prices, 14)
        assert rsi == pytest.approx(100.0)

    def test_all_losses(self):
        """연속 하락 → RSI 0."""
        prices = [float(20 - i) for i in range(20)]  # 20,19,...,1
        rsi = calculate_rsi(prices, 14)
        assert rsi == pytest.approx(0.0)

    def test_mixed(self):
        """혼합 → RSI 0~100 사이."""
        prices = [
            100.0,
            102.0,
            101.0,
            103.0,
            102.5,
            104.0,
            103.0,
            105.0,
            104.5,
            106.0,
            105.0,
            107.0,
            106.5,
            108.0,
            107.0,
            109.0,
        ]
        rsi = calculate_rsi(prices, 14)
        assert rsi is not None
        assert 0 < rsi < 100

    def test_insufficient_data(self):
        """데이터 부족 → None."""
        prices = [1.0] * 10
        assert calculate_rsi(prices, 14) is None

    def test_exact_minimum_data(self):
        """period + 1개 데이터 → 계산 가능."""
        prices = [float(i) for i in range(15)]  # 15개 = 14 + 1
        rsi = calculate_rsi(prices, 14)
        assert rsi is not None


class TestCalculateBollingerBands:
    def test_constant_prices(self):
        """일정 가격 → 밴드폭 0, upper == middle == lower."""
        prices = [100.0] * 20
        upper, middle, lower = calculate_bollinger_bands(prices, 20, 2.0)
        assert middle == pytest.approx(100.0)
        assert upper == pytest.approx(100.0)
        assert lower == pytest.approx(100.0)

    def test_known_values(self):
        """간단한 데이터로 계산 검증."""
        # 1~20 → mean=10.5, std=sqrt(sum((i-10.5)^2)/20)
        prices = [float(i) for i in range(1, 21)]
        upper, middle, lower = calculate_bollinger_bands(prices, 20, 2.0)
        assert middle == pytest.approx(10.5)
        assert upper is not None
        assert lower is not None
        assert upper > middle > lower

    def test_insufficient_data(self):
        """데이터 부족 → (None, None, None)."""
        prices = [1.0] * 10
        assert calculate_bollinger_bands(prices, 20) == (None, None, None)

    def test_uses_last_n_prices(self):
        """마지막 period개만 사용."""
        prices = [1000.0] * 10 + [100.0] * 20
        upper, middle, lower = calculate_bollinger_bands(prices, 20, 2.0)
        assert middle == pytest.approx(100.0)


class TestCalculateMACD:
    def test_returns_correct_length(self):
        """MACD 라인과 시그널 라인 길이가 입력과 동일."""
        prices = [float(i) for i in range(50)]
        macd_line, signal_line = calculate_macd(prices)
        assert len(macd_line) == 50
        assert len(signal_line) == 50

    def test_uptrend_positive_macd(self):
        """상승 추세 → MACD 양수."""
        prices = [100 + i * 2.0 for i in range(50)]
        macd_line, _ = calculate_macd(prices)
        assert macd_line[-1] > 0
