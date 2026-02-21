"""기술적 지표 계산 — Death Cross, MACD Bearish Divergence.

pandas 없이 순수 Python으로 구현.
"""


def calculate_sma(prices: list[float], period: int) -> list[float | None]:
    """단순이동평균 계산.

    Returns:
        prices와 같은 길이의 리스트. period 미만 인덱스는 None.
    """
    result: list[float | None] = [None] * len(prices)
    if len(prices) < period:
        return result

    window_sum = sum(prices[:period])
    result[period - 1] = window_sum / period

    for i in range(period, len(prices)):
        window_sum += prices[i] - prices[i - period]
        result[i] = window_sum / period

    return result


def calculate_ema(prices: list[float], span: int) -> list[float]:
    """지수이동평균 계산.

    Returns:
        prices와 같은 길이의 리스트.
    """
    if not prices:
        return []

    k = 2.0 / (span + 1)
    result = [prices[0]]
    for i in range(1, len(prices)):
        result.append(prices[i] * k + result[-1] * (1 - k))
    return result


def check_death_cross(
    close_prices: list[float],
    short: int = 5,
    long: int = 20,
    gap: float = 0.002,
) -> bool:
    """5MA/20MA 하향 돌파 (데드크로스) 판정.

    Args:
        close_prices: 시간순 종가 (oldest → newest), 최소 long+1개 필요.
        short: 단기 이평 기간
        long: 장기 이평 기간
        gap: 최소 하향 이격률 (0.2%)

    Returns:
        True if 현재 short MA < long MA * (1 - gap) AND 직전 short MA >= long MA.
    """
    if len(close_prices) < long + 1:
        return False

    sma_short = calculate_sma(close_prices, short)
    sma_long = calculate_sma(close_prices, long)

    curr_s = sma_short[-1]
    curr_l = sma_long[-1]
    prev_s = sma_short[-2]
    prev_l = sma_long[-2]

    if curr_s is None or curr_l is None or prev_s is None or prev_l is None:
        return False

    return curr_s < curr_l * (1 - gap) and prev_s >= prev_l


def _calculate_macd(
    close_prices: list[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[list[float], list[float]]:
    """MACD 라인과 시그널 라인 계산.

    Returns:
        (macd_line, signal_line) 각각 close_prices와 같은 길이.
    """
    ema_fast = calculate_ema(close_prices, fast)
    ema_slow = calculate_ema(close_prices, slow)
    macd_line = [f - s for f, s in zip(ema_fast, ema_slow, strict=True)]
    signal_line = calculate_ema(macd_line, signal)
    return macd_line, signal_line


def check_macd_bearish_divergence(
    close_prices: list[float],
    lookback: int = 10,
) -> bool:
    """MACD Bearish Divergence 경고.

    가격은 신고가(또는 유지) 인데 MACD는 하락 → bearish divergence.

    Args:
        close_prices: 시간순 종가 (oldest → newest), 최소 26+lookback개 필요.
        lookback: divergence 비교 구간.

    Returns:
        True if 가격 상승(또는 유지) + MACD histogram 하락.
    """
    min_required = 26 + lookback
    if len(close_prices) < min_required:
        return False

    macd_line, signal_line = _calculate_macd(close_prices)
    histogram = [m - s for m, s in zip(macd_line, signal_line, strict=True)]

    # lookback 구간 내 최고점 vs 현재
    recent_prices = close_prices[-lookback:]
    recent_hist = histogram[-lookback:]

    price_max_idx = recent_prices.index(max(recent_prices))
    hist_at_price_max = recent_hist[price_max_idx]
    hist_current = recent_hist[-1]

    # 가격이 최근 고점 근처(98% 이상)인데 histogram이 감소
    price_near_high = recent_prices[-1] >= max(recent_prices) * 0.98
    hist_declining = hist_current < hist_at_price_max

    return price_near_high and hist_declining
