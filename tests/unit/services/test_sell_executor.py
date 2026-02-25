"""Sell Executor 단위 테스트."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from prime_jennie.domain.enums import SellReason
from prime_jennie.domain.portfolio import Position
from prime_jennie.domain.stock import StockSnapshot
from prime_jennie.domain.trading import OrderResult, SellOrder
from prime_jennie.services.seller.executor import SellExecutor, SellResult


@pytest.fixture(autouse=True)
def _clear_config_cache():
    from prime_jennie.domain.config import get_config

    get_config.cache_clear()
    yield
    get_config.cache_clear()


@pytest.fixture(autouse=True)
def _mock_market_hours():
    with patch("prime_jennie.services.seller.executor._is_market_hours", return_value=True):
        yield


def _make_sell_order(**overrides) -> SellOrder:
    defaults = {
        "stock_code": "005930",
        "stock_name": "삼성전자",
        "sell_reason": SellReason.TRAILING_STOP,
        "current_price": 77000,
        "quantity": 50,
        "timestamp": datetime.now(UTC),
        "buy_price": 70000,
        "profit_pct": 10.0,
    }
    defaults.update(overrides)
    return SellOrder(**defaults)


def _mock_executor():
    """테스트용 SellExecutor 생성."""
    kis = MagicMock()
    kis.get_price.return_value = StockSnapshot(
        stock_code="005930",
        price=77000,
        timestamp=datetime.now(UTC),
    )
    kis.get_positions.return_value = [
        Position(
            stock_code="005930",
            stock_name="삼성전자",
            quantity=100,
            average_buy_price=70000,
            total_buy_amount=7_000_000,
        )
    ]
    kis.sell.return_value = OrderResult(
        success=True,
        order_no="S001234",
        stock_code="005930",
        quantity=50,
        price=77000,
    )

    redis_client = MagicMock()
    redis_client.get.return_value = None  # no emergency stop
    redis_client.set.return_value = True  # lock acquired

    return SellExecutor(kis, redis_client)


class TestSellExecutor:
    def test_successful_sell(self):
        """정상 매도 실행."""
        executor = _mock_executor()
        order = _make_sell_order()
        result = executor.process_signal(order)

        assert result.status == "success"
        assert result.stock_code == "005930"
        assert result.quantity == 50
        assert result.profit_pct == 10.0

    def test_not_holding_skipped(self):
        """미보유 종목 → 스킵."""
        executor = _mock_executor()
        executor._kis.get_positions.return_value = []
        order = _make_sell_order()
        result = executor.process_signal(order)

        assert result.status == "skipped"
        assert "Not holding" in result.reason

    def test_emergency_stop_blocks(self):
        """Emergency stop → 스킵."""
        executor = _mock_executor()
        executor._redis.get.return_value = "1"
        order = _make_sell_order()
        result = executor.process_signal(order)

        assert result.status == "skipped"
        assert "Emergency" in result.reason

    def test_manual_bypasses_emergency(self):
        """MANUAL 매도는 emergency stop 통과."""
        executor = _mock_executor()
        executor._redis.get.return_value = "1"  # emergency active
        order = _make_sell_order(sell_reason=SellReason.MANUAL)
        result = executor.process_signal(order)

        assert result.status == "success"

    def test_lock_failure_skipped(self):
        """분산 락 실패."""
        executor = _mock_executor()
        executor._redis.set.return_value = False
        order = _make_sell_order()
        result = executor.process_signal(order)

        assert result.status == "skipped"
        assert "Lock" in result.reason

    def test_quantity_capped_to_holding(self):
        """매도 수량이 보유 수량 초과 시 보유량으로 제한."""
        executor = _mock_executor()
        order = _make_sell_order(quantity=200)  # 보유 100주
        result = executor.process_signal(order)

        assert result.status == "success"
        assert result.quantity == 100  # capped to holding

    def test_order_failure_error(self):
        """주문 실패."""
        executor = _mock_executor()
        executor._kis.sell.return_value = OrderResult(
            success=False,
            stock_code="005930",
            quantity=50,
            price=77000,
            message="Rejected",
        )
        order = _make_sell_order()
        result = executor.process_signal(order)

        assert result.status == "error"

    def test_stop_loss_sets_cooldown(self):
        """손절 시 쿨다운 설정."""
        executor = _mock_executor()
        order = _make_sell_order(sell_reason=SellReason.STOP_LOSS)
        result = executor.process_signal(order)

        assert result.status == "success"
        # Verify cooldown was set
        executor._redis.setex.assert_called()

    def test_full_sell_cleanup(self):
        """전량 매도 시 Redis 정리."""
        executor = _mock_executor()
        order = _make_sell_order(quantity=100)  # 보유량과 동일
        result = executor.process_signal(order)

        assert result.status == "success"
        # Verify cleanup pipeline was called
        executor._redis.pipeline.assert_called()


class TestSellResult:
    def test_to_dict(self):
        result = SellResult("success", "005930", "삼성전자", "S001234", 50, 77000, 10.0)
        d = result.to_dict()
        assert d["status"] == "success"
        assert d["quantity"] == 50
        assert d["profit_pct"] == 10.0
