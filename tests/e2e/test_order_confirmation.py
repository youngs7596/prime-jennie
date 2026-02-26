"""E2E 주문 체결 확인 및 미체결 취소 테스트.

Mock KIS Gateway → Executor → confirm_order/cancel_order 전체 플로우 검증.

시나리오:
1. 시장가 매수 → 즉시 체결 → 체결가 반영
2. 시장가 매수 → 체결가 슬리피지 → 실제 체결가 반영
3. 시장가 매수 → 미체결 → 취소 → error
4. 지정가 매수 → 타임아웃 → 취소 실패(이미 체결) → 체결가 조회
5. 시장가 매도 → 즉시 체결 → 체결가 반영
6. 시장가 매도 → 슬리피지 → 수익률 재계산
7. 시장가 매도 → 미체결 → 취소 → error
"""

from unittest.mock import patch

import pytest

from prime_jennie.domain.enums import SignalType

pytestmark = pytest.mark.e2e

_SAMSUNG_POSITION = {
    "stock_code": "005930",
    "stock_name": "삼성전자",
    "quantity": 100,
    "average_buy_price": 60000,
    "total_buy_amount": 6_000_000,
}


@pytest.fixture(autouse=True)
def _fast_sleep():
    """time.sleep 제거 — confirm_order 폴링 + 지정가 타임아웃 즉시 통과."""
    with (
        patch("prime_jennie.infra.kis.client.time.sleep"),
        patch("prime_jennie.services.buyer.executor.time.sleep"),
    ):
        yield


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. 시장가 매수 → 즉시 체결
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_buy_market_filled_immediately(buy_executor, mock_gateway_state, make_buy_signal):
    """시장가 매수 → confirm_order 즉시 체결 → 성공."""
    mock_gateway_state.prices["005930"] = 65000
    mock_gateway_state.cash_balance = 100_000_000

    signal = make_buy_signal(stock_code="005930", hybrid_score=75.0)
    result = buy_executor.process_signal(signal)

    assert result.status == "success"
    assert result.stock_code == "005930"
    assert result.quantity > 0
    assert result.price == 65000
    assert result.order_no.startswith("MOCK-")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. 시장가 매수 → 체결가 슬리피지
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_buy_market_fill_price_slippage(buy_executor, mock_gateway_state, make_buy_signal):
    """스냅샷 65000 → 실제 체결 65200 → result.price에 실체결가 반영."""
    mock_gateway_state.prices["005930"] = 65000
    mock_gateway_state.cash_balance = 100_000_000
    mock_gateway_state.fill_price_override = 65200  # 시장가 슬리피지

    signal = make_buy_signal(stock_code="005930", hybrid_score=75.0)
    result = buy_executor.process_signal(signal)

    assert result.status == "success"
    assert result.price == 65200  # 슬리피지 반영


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. 시장가 매수 → 미체결 → 취소 → error
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_buy_market_not_filled_then_cancel(buy_executor, mock_gateway_state, make_buy_signal):
    """시장가 매수 → 3회 폴링 미체결 → cancel → error."""
    mock_gateway_state.prices["005930"] = 65000
    mock_gateway_state.cash_balance = 100_000_000
    mock_gateway_state.default_filled = False  # 미체결 시뮬레이션

    signal = make_buy_signal(stock_code="005930", hybrid_score=75.0)
    result = buy_executor.process_signal(signal)

    assert result.status == "error"
    assert "not filled" in result.reason.lower()
    assert "cancelled" in result.reason.lower()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. 지정가 매수 → 타임아웃 → 취소 실패(이미 체결) → 체결가 조회
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_buy_limit_cancel_fail_means_filled(buy_executor, mock_gateway_state, make_buy_signal):
    """지정가 매수 → sleep(timeout) → cancel 실패 → confirm_order → 체결가 반영.

    Momentum 전략 지정가 주문에서 cancel이 실패하면 이미 체결된 것.
    confirm_order로 실제 체결가를 조회하여 반영.
    """
    mock_gateway_state.prices["005930"] = 65000
    mock_gateway_state.cash_balance = 100_000_000
    mock_gateway_state.cancel_should_fail = True  # 취소 실패 = 이미 체결
    mock_gateway_state.fill_price_override = 64500  # 지정가 체결가

    signal = make_buy_signal(
        stock_code="005930",
        hybrid_score=75.0,
        signal_type=SignalType.MOMENTUM,  # 지정가 주문 트리거
    )
    result = buy_executor.process_signal(signal)

    assert result.status == "success"
    assert result.price == 64500  # confirm_order로 조회한 체결가


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. 시장가 매도 → 즉시 체결
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_sell_market_filled_immediately(sell_executor, mock_gateway_state, make_sell_order):
    """시장가 매도 → confirm_order 즉시 체결 → 성공."""
    mock_gateway_state.positions = [_SAMSUNG_POSITION.copy()]
    mock_gateway_state.prices["005930"] = 70000

    order = make_sell_order(stock_code="005930", quantity=100, current_price=70000)
    result = sell_executor.process_signal(order)

    assert result.status == "success"
    assert result.stock_code == "005930"
    assert result.quantity == 100
    assert result.order_no.startswith("MOCK-")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 6. 시장가 매도 → 슬리피지 → 수익률 재계산
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_sell_market_slippage_recalculates_profit(sell_executor, mock_gateway_state, make_sell_order):
    """스냅샷 70000 → 실체결 71000 → 수익률 재계산.

    buy_price=60000, 스냅샷=70000 → 16.67%
    실체결=71000 → (71000-60000)/60000*100 = 18.33%
    """
    mock_gateway_state.positions = [_SAMSUNG_POSITION.copy()]
    mock_gateway_state.prices["005930"] = 70000
    mock_gateway_state.fill_price_override = 71000  # 슬리피지 (유리한 방향)

    order = make_sell_order(
        stock_code="005930",
        quantity=100,
        current_price=70000,
        buy_price=60000,
    )
    result = sell_executor.process_signal(order)

    assert result.status == "success"
    assert result.price == 71000  # 실체결가
    assert result.profit_pct == pytest.approx(18.33, abs=0.01)  # 재계산된 수익률


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 7. 시장가 매도 → 미체결 → 취소 → error
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_sell_market_not_filled_then_cancel(sell_executor, mock_gateway_state, make_sell_order):
    """시장가 매도 → 3회 폴링 미체결 → cancel → error (매도 미체결은 심각)."""
    mock_gateway_state.positions = [_SAMSUNG_POSITION.copy()]
    mock_gateway_state.prices["005930"] = 70000
    mock_gateway_state.default_filled = False  # 미체결

    order = make_sell_order(stock_code="005930", quantity=100, current_price=70000)
    result = sell_executor.process_signal(order)

    assert result.status == "error"
    assert "not filled" in result.reason.lower()
    assert "cancelled" in result.reason.lower()
