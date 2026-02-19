"""Buy Executor 단위 테스트."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from prime_jennie.domain.enums import MarketRegime, SignalType, TradeTier
from prime_jennie.domain.portfolio import Position
from prime_jennie.domain.stock import StockSnapshot
from prime_jennie.domain.trading import BuySignal, OrderResult
from prime_jennie.services.buyer.executor import BuyExecutor, ExecutionResult, _align_tick_size


@pytest.fixture(autouse=True)
def _clear_config_cache():
    from prime_jennie.domain.config import get_config

    get_config.cache_clear()
    yield
    get_config.cache_clear()


def _make_signal(**overrides) -> BuySignal:
    defaults = {
        "stock_code": "005930",
        "stock_name": "삼성전자",
        "signal_type": SignalType.GOLDEN_CROSS,
        "signal_price": 72000,
        "llm_score": 70.0,
        "hybrid_score": 72.0,
        "is_tradable": True,
        "trade_tier": TradeTier.TIER1,
        "market_regime": MarketRegime.BULL,
        "timestamp": datetime.now(UTC),
    }
    defaults.update(overrides)
    return BuySignal(**defaults)


def _mock_executor():
    """테스트용 BuyExecutor 생성 (KIS + Redis mocked)."""
    kis = MagicMock()
    kis.get_price.return_value = StockSnapshot(
        stock_code="005930",
        price=72000,
        timestamp=datetime.now(UTC),
    )
    kis.get_positions.return_value = []
    kis.get_balance.return_value = {"cash_balance": 10_000_000}
    kis.buy.return_value = OrderResult(
        success=True,
        order_no="0001234",
        stock_code="005930",
        quantity=10,
        price=72000,
    )

    redis_client = MagicMock()
    redis_client.get.return_value = None  # no emergency stop
    redis_client.set.return_value = True  # lock acquired

    return BuyExecutor(kis, redis_client)


class TestAlignTickSize:
    def test_under_2000(self):
        assert _align_tick_size(1234) == 1234

    def test_2000_to_5000(self):
        assert _align_tick_size(3003) == 3000

    def test_5000_to_20000(self):
        assert _align_tick_size(7777) == 7770

    def test_20000_to_50000(self):
        assert _align_tick_size(35025) == 35000

    def test_50000_to_200000(self):
        assert _align_tick_size(72150) == 72100

    def test_200000_to_500000(self):
        assert _align_tick_size(250300) == 250000

    def test_above_500000(self):
        assert _align_tick_size(512000) == 512000


class TestBuyExecutor:
    def test_successful_buy(self):
        """정상 매수 실행."""
        executor = _mock_executor()
        signal = _make_signal()
        result = executor.process_signal(signal)

        assert result.status == "success"
        assert result.stock_code == "005930"
        assert result.quantity > 0

    def test_blocked_tier_skipped(self):
        """BLOCKED 티어 → 스킵."""
        executor = _mock_executor()
        signal = _make_signal(trade_tier=TradeTier.BLOCKED)
        result = executor.process_signal(signal)

        assert result.status == "skipped"
        assert "BLOCKED" in result.reason

    def test_hard_floor_skipped(self):
        """Hard floor 미달 → 스킵."""
        executor = _mock_executor()
        signal = _make_signal(hybrid_score=30.0)
        result = executor.process_signal(signal)

        assert result.status == "skipped"
        assert "Hard floor" in result.reason

    def test_already_holding_skipped(self):
        """이미 보유 중 → 스킵."""
        executor = _mock_executor()
        executor._kis.get_positions.return_value = [
            Position(
                stock_code="005930",
                stock_name="삼성전자",
                quantity=100,
                average_buy_price=70000,
                total_buy_amount=7_000_000,
            )
        ]
        signal = _make_signal()
        result = executor.process_signal(signal)

        assert result.status == "skipped"
        assert "Already holding" in result.reason

    def test_emergency_stop(self):
        """Emergency stop 활성 시 스킵."""
        executor = _mock_executor()
        executor._redis.get.return_value = "1"  # emergency stop
        signal = _make_signal()
        result = executor.process_signal(signal)

        assert result.status == "skipped"
        assert "Emergency" in result.reason

    def test_lock_failure_skipped(self):
        """분산 락 실패 시 스킵."""
        executor = _mock_executor()
        executor._redis.set.return_value = False  # lock failed
        signal = _make_signal()
        result = executor.process_signal(signal)

        assert result.status == "skipped"
        assert "Lock" in result.reason

    def test_portfolio_full_skipped(self):
        """포트폴리오 가득 참."""
        executor = _mock_executor()
        # 10개 종목 보유 중
        positions = [
            Position(
                stock_code=f"{i:06d}",
                stock_name=f"Stock{i}",
                quantity=100,
                average_buy_price=10000,
                total_buy_amount=1_000_000,
            )
            for i in range(10)
        ]
        executor._kis.get_positions.return_value = positions
        signal = _make_signal()
        result = executor.process_signal(signal)

        assert result.status == "skipped"
        assert "Portfolio full" in result.reason

    def test_order_failure_error(self):
        """주문 실패 → error."""
        executor = _mock_executor()
        executor._kis.buy.return_value = OrderResult(
            success=False,
            stock_code="005930",
            quantity=10,
            price=72000,
            message="Order rejected",
        )
        signal = _make_signal()
        result = executor.process_signal(signal)

        assert result.status == "error"


class TestExecutionResult:
    def test_to_dict(self):
        result = ExecutionResult("success", "005930", "삼성전자", "001234", 10, 72000)
        d = result.to_dict()
        assert d["status"] == "success"
        assert d["stock_code"] == "005930"
        assert d["quantity"] == 10
