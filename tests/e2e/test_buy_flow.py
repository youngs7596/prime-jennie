"""E2E 매수 파이프라인 테스트.

BuyExecutor.process_signal() → Mock KIS Gateway 주문 → DB 저장 → Redis 알림.
"""

import pytest
from sqlmodel import Session, select

from prime_jennie.domain.enums import TradeTier
from prime_jennie.domain.notification import TradeNotification
from prime_jennie.infra.database.models import PositionDB, TradeLogDB
from prime_jennie.infra.redis.streams import TypedStreamPublisher
from prime_jennie.services.buyer.app import _persist_buy
from prime_jennie.services.buyer.executor import EMERGENCY_STOP_KEY

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# 1. 정상 매수 성공
# ---------------------------------------------------------------------------


def test_buy_success_basic(buy_executor, mock_gateway_state, make_buy_signal):
    """정상 매수 → ExecutionResult.status == 'success'."""
    mock_gateway_state.prices["005930"] = 65000
    mock_gateway_state.cash_balance = 100_000_000

    signal = make_buy_signal(stock_code="005930", hybrid_score=75.0)
    result = buy_executor.process_signal(signal)

    assert result.status == "success"
    assert result.stock_code == "005930"
    assert result.quantity > 0
    assert result.price > 0
    assert result.order_no.startswith("MOCK-")


# ---------------------------------------------------------------------------
# 2. 매수 후 DB 저장
# ---------------------------------------------------------------------------


def test_buy_persists_to_db(buy_executor, mock_gateway_state, test_engine, make_buy_signal):
    """매수 성공 후 _persist_buy → trade_logs + positions 테이블 확인."""
    mock_gateway_state.prices["005930"] = 65000
    mock_gateway_state.cash_balance = 100_000_000

    signal = make_buy_signal(stock_code="005930", hybrid_score=75.0)
    result = buy_executor.process_signal(signal)
    assert result.status == "success"

    # DB 저장 (app 모듈의 함수 직접 호출)
    _persist_buy(signal, result)

    with Session(test_engine) as session:
        trades = list(session.exec(select(TradeLogDB)).all())
        assert len(trades) == 1
        assert trades[0].trade_type == "BUY"
        assert trades[0].stock_code == "005930"
        assert trades[0].quantity == result.quantity
        assert trades[0].price == result.price

        pos = session.get(PositionDB, "005930")
        assert pos is not None
        assert pos.quantity == result.quantity
        assert pos.average_buy_price == result.price


# ---------------------------------------------------------------------------
# 3. 매수 후 알림 발행
# ---------------------------------------------------------------------------


def test_buy_publishes_notification(buy_executor, mock_gateway_state, test_redis, make_buy_signal):
    """매수 성공 후 Redis stream:trade-notifications에 메시지 발행."""
    mock_gateway_state.prices["005930"] = 65000
    mock_gateway_state.cash_balance = 100_000_000

    signal = make_buy_signal(stock_code="005930", hybrid_score=75.0)
    result = buy_executor.process_signal(signal)
    assert result.status == "success"

    # 알림 발행 (notifier 직접 생성)
    stream_key = "stream:trade-notifications"
    notifier = TypedStreamPublisher(test_redis, stream_key, TradeNotification)

    from prime_jennie.services.buyer import app as buyer_app

    original_notifier = buyer_app._notifier
    buyer_app._notifier = notifier
    try:
        from prime_jennie.services.buyer.app import _notify_buy

        _notify_buy(signal, result)
    finally:
        buyer_app._notifier = original_notifier

    # Redis stream 확인
    messages = test_redis.xrange(stream_key)
    assert len(messages) == 1
    msg_id, data = messages[0]
    assert "payload" in data


# ---------------------------------------------------------------------------
# 4. BLOCKED 티어 스킵
# ---------------------------------------------------------------------------


def test_buy_blocked_tier_skip(buy_executor, mock_gateway_state, make_buy_signal):
    """TradeTier.BLOCKED → status == 'skipped'."""
    mock_gateway_state.prices["005930"] = 65000

    signal = make_buy_signal(trade_tier=TradeTier.BLOCKED)
    result = buy_executor.process_signal(signal)

    assert result.status == "skipped"
    assert "BLOCKED" in result.reason


# ---------------------------------------------------------------------------
# 5. Hard floor 스킵
# ---------------------------------------------------------------------------


def test_buy_hard_floor_skip(buy_executor, mock_gateway_state, make_buy_signal):
    """hybrid_score < hard_floor_score(40) → status == 'skipped'."""
    signal = make_buy_signal(hybrid_score=30.0)
    result = buy_executor.process_signal(signal)

    assert result.status == "skipped"
    assert "Hard floor" in result.reason


# ---------------------------------------------------------------------------
# 6. 이미 보유 중 스킵
# ---------------------------------------------------------------------------


def test_buy_already_holding_skip(buy_executor, mock_gateway_state, make_buy_signal):
    """이미 보유 종목 → status == 'skipped'."""
    mock_gateway_state.positions = [
        {
            "stock_code": "005930",
            "stock_name": "삼성전자",
            "quantity": 100,
            "average_buy_price": 60000,
            "total_buy_amount": 6_000_000,
        }
    ]

    signal = make_buy_signal(stock_code="005930", hybrid_score=75.0)
    result = buy_executor.process_signal(signal)

    assert result.status == "skipped"
    assert "Already holding" in result.reason


# ---------------------------------------------------------------------------
# 7. Emergency stop 스킵
# ---------------------------------------------------------------------------


def test_buy_emergency_stop(buy_executor, test_redis, make_buy_signal):
    """Redis emergency stop 설정 → status == 'skipped'."""
    test_redis.set(EMERGENCY_STOP_KEY, "1")

    signal = make_buy_signal(hybrid_score=75.0)
    result = buy_executor.process_signal(signal)

    assert result.status == "skipped"
    assert "Emergency" in result.reason


# ---------------------------------------------------------------------------
# 8. Gateway 주문 실패
# ---------------------------------------------------------------------------


def test_buy_order_failure(buy_executor, mock_gateway_state, make_buy_signal):
    """gateway order_should_fail=True → status == 'error'."""
    mock_gateway_state.prices["005930"] = 65000
    mock_gateway_state.cash_balance = 100_000_000
    mock_gateway_state.order_should_fail = True
    mock_gateway_state.order_fail_message = "Insufficient funds"

    signal = make_buy_signal(stock_code="005930", hybrid_score=75.0)
    result = buy_executor.process_signal(signal)

    assert result.status == "error"
    assert "Order failed" in result.reason
