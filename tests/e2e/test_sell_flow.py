"""E2E 매도 파이프라인 테스트.

SellExecutor.process_signal() → Mock KIS Gateway 주문 → DB 저장 → Redis 알림.
"""

import pytest
from sqlmodel import Session, select

from prime_jennie.domain.enums import SellReason
from prime_jennie.domain.notification import TradeNotification
from prime_jennie.infra.database.models import PositionDB, TradeLogDB
from prime_jennie.infra.redis.streams import TypedStreamPublisher
from prime_jennie.services.seller.app import _notify_sell, _persist_sell
from prime_jennie.services.seller.executor import COOLDOWN_PREFIX, EMERGENCY_STOP_KEY

pytestmark = pytest.mark.e2e

# ---------------------------------------------------------------------------
# Helper: 보유 포지션 설정
# ---------------------------------------------------------------------------

_SAMSUNG_POSITION = {
    "stock_code": "005930",
    "stock_name": "삼성전자",
    "quantity": 100,
    "average_buy_price": 60000,
    "total_buy_amount": 6_000_000,
}


def _seed_position_db(engine, stock_code="005930", stock_name="삼성전자", quantity=100, avg_price=60000):
    """DB에 보유 포지션 시드."""
    with Session(engine) as session:
        pos = PositionDB(
            stock_code=stock_code,
            stock_name=stock_name,
            quantity=quantity,
            average_buy_price=avg_price,
            total_buy_amount=quantity * avg_price,
        )
        session.add(pos)
        session.commit()


# ---------------------------------------------------------------------------
# 1. 정상 매도 성공
# ---------------------------------------------------------------------------


def test_sell_success_basic(sell_executor, mock_gateway_state, make_sell_order):
    """보유 종목 매도 → status == 'success'."""
    mock_gateway_state.positions = [_SAMSUNG_POSITION.copy()]
    mock_gateway_state.prices["005930"] = 70000

    order = make_sell_order(stock_code="005930", quantity=100, current_price=70000)
    result = sell_executor.process_signal(order)

    assert result.status == "success"
    assert result.stock_code == "005930"
    assert result.quantity == 100
    assert result.order_no.startswith("MOCK-")
    assert result.profit_pct > 0  # 60000 → 70000


# ---------------------------------------------------------------------------
# 2. 매도 후 DB 저장
# ---------------------------------------------------------------------------


def test_sell_persists_to_db(sell_executor, mock_gateway_state, test_engine, make_sell_order):
    """매도 성공 후 _persist_sell → trade_logs SELL + position 감소."""
    mock_gateway_state.positions = [_SAMSUNG_POSITION.copy()]
    mock_gateway_state.prices["005930"] = 70000

    # DB에 position 시드
    _seed_position_db(test_engine)

    order = make_sell_order(stock_code="005930", quantity=100, current_price=70000, buy_price=60000)
    result = sell_executor.process_signal(order)
    assert result.status == "success"

    # DB 저장
    _persist_sell(order, result)

    with Session(test_engine) as session:
        trades = list(session.exec(select(TradeLogDB)).all())
        assert len(trades) == 1
        assert trades[0].trade_type == "SELL"
        assert trades[0].stock_code == "005930"
        assert trades[0].quantity == 100

        # 전량 매도 → position 삭제
        pos = session.get(PositionDB, "005930")
        assert pos is None


# ---------------------------------------------------------------------------
# 3. 매도 후 알림 발행
# ---------------------------------------------------------------------------


def test_sell_publishes_notification(sell_executor, mock_gateway_state, test_redis, make_sell_order):
    """매도 성공 후 Redis stream 메시지 확인."""
    mock_gateway_state.positions = [_SAMSUNG_POSITION.copy()]
    mock_gateway_state.prices["005930"] = 70000

    order = make_sell_order(stock_code="005930", quantity=100, current_price=70000)
    result = sell_executor.process_signal(order)
    assert result.status == "success"

    stream_key = "stream:trade-notifications"
    notifier = TypedStreamPublisher(test_redis, stream_key, TradeNotification)
    _notify_sell(order, result, notifier)

    messages = test_redis.xrange(stream_key)
    assert len(messages) == 1
    msg_id, data = messages[0]
    assert "payload" in data


# ---------------------------------------------------------------------------
# 4. 미보유 종목 매도 → 스킵
# ---------------------------------------------------------------------------


def test_sell_not_holding_skip(sell_executor, mock_gateway_state, make_sell_order):
    """미보유 종목 → status == 'skipped'."""
    mock_gateway_state.positions = []  # 빈 포트폴리오

    order = make_sell_order(stock_code="005930", quantity=100)
    result = sell_executor.process_signal(order)

    assert result.status == "skipped"
    assert "Not holding" in result.reason


# ---------------------------------------------------------------------------
# 5. Emergency stop → 매도 차단
# ---------------------------------------------------------------------------


def test_sell_emergency_stop_blocks(sell_executor, mock_gateway_state, test_redis, make_sell_order):
    """emergency stop 설정 → 비-MANUAL 매도 차단."""
    mock_gateway_state.positions = [_SAMSUNG_POSITION.copy()]
    test_redis.set(EMERGENCY_STOP_KEY, "1")

    order = make_sell_order(sell_reason=SellReason.PROFIT_TARGET)
    result = sell_executor.process_signal(order)

    assert result.status == "skipped"
    assert "Emergency" in result.reason


# ---------------------------------------------------------------------------
# 6. MANUAL은 emergency stop 무시
# ---------------------------------------------------------------------------


def test_sell_manual_bypasses_emergency(sell_executor, mock_gateway_state, test_redis, make_sell_order):
    """MANUAL reason → emergency stop 무시하고 매도 진행."""
    mock_gateway_state.positions = [_SAMSUNG_POSITION.copy()]
    mock_gateway_state.prices["005930"] = 70000
    test_redis.set(EMERGENCY_STOP_KEY, "1")

    order = make_sell_order(sell_reason=SellReason.MANUAL, quantity=100)
    result = sell_executor.process_signal(order)

    assert result.status == "success"


# ---------------------------------------------------------------------------
# 7. Stop-loss → 쿨다운 Redis 키 생성
# ---------------------------------------------------------------------------


def test_sell_stop_loss_sets_cooldown(sell_executor, mock_gateway_state, test_redis, make_sell_order):
    """STOP_LOSS reason → stoploss_cooldown:{code} Redis key 생성."""
    mock_gateway_state.positions = [_SAMSUNG_POSITION.copy()]
    mock_gateway_state.prices["005930"] = 55000  # 손실

    order = make_sell_order(sell_reason=SellReason.STOP_LOSS, quantity=100, current_price=55000)
    result = sell_executor.process_signal(order)
    assert result.status == "success"

    cooldown_key = f"{COOLDOWN_PREFIX}005930"
    assert test_redis.get(cooldown_key) is not None
    # TTL 확인 (3일 = 259200초)
    ttl = test_redis.ttl(cooldown_key)
    assert ttl > 0


# ---------------------------------------------------------------------------
# 8. 전량 매도 시 Redis 상태 정리
# ---------------------------------------------------------------------------


def test_sell_full_exit_cleanup(sell_executor, mock_gateway_state, test_redis, make_sell_order):
    """전량 매도 → watermark/scale_out/rsi_sold/profit_floor 삭제."""
    mock_gateway_state.positions = [_SAMSUNG_POSITION.copy()]
    mock_gateway_state.prices["005930"] = 70000

    # 사전에 Redis 상태 키 설정
    test_redis.set("watermark:005930", "72000")
    test_redis.set("scale_out:005930", "1")
    test_redis.set("rsi_sold:005930", "1")
    test_redis.set("profit_floor:005930", "1")

    order = make_sell_order(quantity=100)  # 전량 (position qty=100)
    result = sell_executor.process_signal(order)
    assert result.status == "success"

    # 모든 상태 키가 삭제되었는지 확인
    assert test_redis.get("watermark:005930") is None
    assert test_redis.get("scale_out:005930") is None
    assert test_redis.get("rsi_sold:005930") is None
    assert test_redis.get("profit_floor:005930") is None
