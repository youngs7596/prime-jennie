"""Portfolio Correlation Check — 고상관 종목 동시 보유 방지.

보유 종목과 후보 종목 간 Pearson 상관계수를 계산하여
threshold(기본 0.85) 이상이면 매수 차단.
"""

import logging

import numpy as np

from prime_jennie.domain.portfolio import Position

logger = logging.getLogger(__name__)


def calculate_correlation(
    prices_a: list[float],
    prices_b: list[float],
    min_periods: int = 20,
) -> float | None:
    """두 종목의 종가 시계열로 Pearson 상관계수 계산.

    log returns 기반. 데이터 부족 시 None 반환.
    """
    min_len = min(len(prices_a), len(prices_b))
    if min_len < min_periods + 1:
        return None

    # 길이 맞춤 (최근 기준)
    a = np.array(prices_a[-min_len:], dtype=np.float64)
    b = np.array(prices_b[-min_len:], dtype=np.float64)

    # log returns
    with np.errstate(divide="ignore", invalid="ignore"):
        ret_a = np.diff(np.log(a))
        ret_b = np.diff(np.log(b))

    # NaN/Inf 제거
    valid = np.isfinite(ret_a) & np.isfinite(ret_b)
    if valid.sum() < min_periods:
        return None

    ret_a = ret_a[valid]
    ret_b = ret_b[valid]

    # Pearson correlation
    corr_matrix = np.corrcoef(ret_a, ret_b)
    corr = float(corr_matrix[0, 1])

    if not np.isfinite(corr):
        return None
    return corr


def check_portfolio_correlation(
    candidate_code: str,
    candidate_prices: list[float],
    positions: list[Position],
    price_lookup_fn: callable,
    block_threshold: float = 0.85,
) -> tuple[bool, float, str]:
    """후보 종목과 보유 종목 간 상관관계 체크.

    Args:
        candidate_code: 매수 후보 종목 코드
        candidate_prices: 후보 종목 일봉 종가 리스트
        positions: 현재 보유 포지션 리스트
        price_lookup_fn: stock_code -> list[float] (일봉 종가)
        block_threshold: 차단 임계값

    Returns:
        (passed, max_corr, message)
        - passed: True면 통과, False면 차단
        - max_corr: 가장 높은 상관계수
        - message: 설명
    """
    max_corr = 0.0
    max_corr_code = ""

    for pos in positions:
        if pos.stock_code == candidate_code:
            continue
        try:
            held_prices = price_lookup_fn(pos.stock_code)
        except Exception:
            logger.debug("[%s] Price lookup failed for correlation", pos.stock_code)
            continue

        corr = calculate_correlation(candidate_prices, held_prices)
        if corr is not None and corr > max_corr:
            max_corr = corr
            max_corr_code = pos.stock_code

    if max_corr >= block_threshold:
        return (
            False,
            max_corr,
            f"High correlation {max_corr:.2f} with {max_corr_code} (threshold={block_threshold})",
        )

    return (True, max_corr, f"Max correlation {max_corr:.2f}")
