"""Price Monitor (Redis Stream tick consumer) 단위 테스트."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from prime_jennie.domain.enums import SellReason
from prime_jennie.domain.portfolio import Position
from prime_jennie.services.monitor.app import PriceMonitor


@pytest.fixture(autouse=True)
def _clear_config_cache():
    from prime_jennie.domain.config import get_config

    get_config.cache_clear()
    yield
    get_config.cache_clear()


def _make_position(**overrides) -> Position:
    defaults = {
        "stock_code": "005930",
        "stock_name": "삼성전자",
        "quantity": 100,
        "average_buy_price": 70000,
        "total_buy_amount": 7_000_000,
        "current_price": 72000,
        "bought_at": datetime(2026, 2, 15, tzinfo=UTC),
    }
    defaults.update(overrides)
    return Position(**defaults)


def _make_monitor(positions: list[Position] | None = None) -> PriceMonitor:
    """Mock 의존성으로 PriceMonitor 생성."""
    kis = MagicMock()
    kis.get_positions.return_value = positions or []
    kis.get_daily_prices.return_value = []

    r = MagicMock()
    r.get.return_value = None
    r.pipeline.return_value = MagicMock()

    monitor = PriceMonitor(kis_client=kis, redis_client=r)
    monitor._publisher = MagicMock()
    return monitor


class TestRefreshPositions:
    def test_loads_positions_into_dict(self):
        pos = _make_position()
        monitor = _make_monitor([pos])

        codes = monitor.refresh_positions()

        assert codes == ["005930"]
        assert "005930" in monitor._positions
        assert monitor._positions["005930"].stock_code == "005930"

    def test_cleans_up_removed_positions(self):
        pos_a = _make_position(stock_code="005930")
        pos_b = _make_position(stock_code="000660", stock_name="SK하이닉스")

        monitor = _make_monitor([pos_a, pos_b])
        monitor.refresh_positions()
        assert len(monitor._positions) == 2

        # 두 번째 refresh: pos_b가 사라짐
        monitor._kis.get_positions.return_value = [pos_a]
        codes = monitor.refresh_positions()

        assert "000660" not in monitor._positions
        assert "000660" not in monitor._rsi_cache
        assert "000660" not in monitor._atr_cache
        assert "000660" not in monitor._indicator_cache
        assert set(codes) == {"005930"}

    def test_computes_indicators_for_each_position(self):
        pos = _make_position()
        monitor = _make_monitor([pos])

        with patch.object(monitor, "_compute_all_indicators") as mock_compute:
            monitor.refresh_positions()
            mock_compute.assert_called_once_with("005930")

    def test_returns_old_codes_on_api_failure(self):
        pos = _make_position()
        monitor = _make_monitor([pos])
        monitor.refresh_positions()

        # API 실패
        monitor._kis.get_positions.side_effect = Exception("network error")
        codes = monitor.refresh_positions()

        assert "005930" in codes
        assert "005930" in monitor._positions


class TestProcessTick:
    def test_ignores_non_held_stock(self):
        monitor = _make_monitor()
        # 보유하지 않은 종목 → 아무 일도 일어나지 않음
        monitor.process_tick("999999", 50000)

    def test_updates_current_price(self):
        pos = _make_position(current_price=72000)
        monitor = _make_monitor([pos])
        monitor.refresh_positions()

        monitor.process_tick("005930", 73000)

        assert monitor._positions["005930"].current_price == 73000

    def test_triggers_sell_on_hard_stop(self):
        """-10% 이하 가격 → 매도 시그널 발행."""
        pos = _make_position(
            current_price=70000,
            average_buy_price=70000,
            quantity=100,
        )
        monitor = _make_monitor([pos])
        monitor.refresh_positions()

        # -11.4% 가격
        monitor.process_tick("005930", 62000)

        # sell 시그널이 publisher를 통해 발행됨
        monitor._publisher.publish.assert_called_once()
        order = monitor._publisher.publish.call_args[0][0]
        assert order.stock_code == "005930"
        assert order.sell_reason == SellReason.STOP_LOSS
        assert order.quantity == 100

    def test_no_sell_for_normal_price(self):
        """정상 가격 범위 → 매도 시그널 없음."""
        pos = _make_position(
            current_price=70000,
            average_buy_price=70000,
            quantity=100,
        )
        monitor = _make_monitor([pos])
        monitor.refresh_positions()

        # +1.4% (정상 범위)
        monitor.process_tick("005930", 71000)

        monitor._publisher.publish.assert_not_called()

    def test_full_sell_removes_position(self):
        """전량 매도 시 인메모리 포지션 제거."""
        pos = _make_position(
            current_price=70000,
            average_buy_price=70000,
            quantity=100,
        )
        monitor = _make_monitor([pos])
        monitor.refresh_positions()

        # Hard stop → 전량 매도
        monitor.process_tick("005930", 62000)

        assert "005930" not in monitor._positions
        assert "005930" not in monitor._rsi_cache
        assert "005930" not in monitor._atr_cache
        assert "005930" not in monitor._indicator_cache

    def test_evaluation_exception_is_caught(self):
        """_evaluate_position 예외 시 크래시하지 않음."""
        pos = _make_position()
        monitor = _make_monitor([pos])
        monitor.refresh_positions()

        with patch.object(
            monitor,
            "_evaluate_position",
            side_effect=RuntimeError("boom"),
        ):
            # 예외가 전파되지 않아야 함
            monitor.process_tick("005930", 72000)


class TestCaches:
    def test_rsi_from_cache_not_recomputed_on_tick(self):
        """process_tick은 RSI를 재계산하지 않고 캐시를 사용."""
        pos = _make_position()
        monitor = _make_monitor([pos])
        monitor._rsi_cache["005930"] = 60.0
        monitor._atr_cache["005930"] = 1400.0
        monitor._positions["005930"] = pos

        with patch.object(monitor, "_compute_all_indicators") as mock_compute:
            monitor.process_tick("005930", 72000)
            mock_compute.assert_not_called()

    def test_atr_cache_populated_on_refresh(self):
        """refresh_positions에서 ATR 캐시가 채워짐."""
        pos = _make_position()
        monitor = _make_monitor([pos])

        with patch.object(monitor, "_compute_all_indicators") as mock_compute:
            monitor.refresh_positions()
            mock_compute.assert_called_once_with("005930")

    def test_indicator_cache_populated_on_refresh(self):
        """refresh_positions에서 indicator 캐시가 채워짐."""
        pos = _make_position()
        monitor = _make_monitor([pos])

        # _compute_all_indicators가 호출될 때 실제로 캐시 설정
        monitor.refresh_positions()
        # daily_prices가 빈 리스트이므로 기본값
        assert "005930" in monitor._indicator_cache


class TestForcedLiquidation:
    def test_armed_and_target_triggers_sell(self):
        """ARM ON + 대상 종목 → 강제 청산 시그널 발행."""
        pos = _make_position(stock_code="015760", stock_name="한국전력", quantity=510)
        monitor = _make_monitor([pos])
        monitor.refresh_positions()

        # ARM=ON, 대상 종목 등록 상태
        monitor._redis.get.return_value = "1"  # FORCED_LIQUIDATION_ARMED
        monitor._redis.sismember.return_value = True
        monitor._redis.scard.return_value = 0  # SET이 비게 됨

        monitor.process_tick("015760", 45000)

        monitor._publisher.publish.assert_called_once()
        order = monitor._publisher.publish.call_args[0][0]
        assert order.stock_code == "015760"
        assert order.sell_reason == SellReason.FORCED_LIQUIDATION
        assert order.quantity == 510
        # 인메모리 캐시 제거
        assert "015760" not in monitor._positions

    def test_armed_but_not_target_proceeds_normally(self):
        """ARM ON + 대상이 아닌 종목 → 정상 exit rule 평가."""
        pos = _make_position(stock_code="005930", stock_name="삼성전자")
        monitor = _make_monitor([pos])
        monitor.refresh_positions()

        # ARM=ON이지만 005930은 대상이 아님
        def mock_get(key):
            if key == "forced_liquidation:armed":
                return "1"
            return None

        monitor._redis.get.side_effect = mock_get
        monitor._redis.sismember.return_value = False

        monitor.process_tick("005930", 71000)

        # 강제 청산이 아닌 정상 평가 (이 가격은 매도 조건 안 됨)
        monitor._publisher.publish.assert_not_called()

    def test_not_armed_skips_forced_liquidation(self):
        """ARM OFF → 강제 청산 체크 건너뜀."""
        pos = _make_position(stock_code="015760", stock_name="한국전력")
        monitor = _make_monitor([pos])
        monitor.refresh_positions()

        monitor._redis.get.return_value = None  # ARM OFF

        monitor.process_tick("015760", 45000)

        # sismember는 호출되지 않아야 함 (ARM이 꺼져있으므로)
        monitor._redis.sismember.assert_not_called()

    def test_auto_disarm_when_set_empty(self):
        """마지막 종목 매도 후 SET 비면 ARM 자동 해제."""
        pos = _make_position(stock_code="015760", stock_name="한국전력")
        monitor = _make_monitor([pos])
        monitor.refresh_positions()

        monitor._redis.get.return_value = "1"
        monitor._redis.sismember.return_value = True
        monitor._redis.scard.return_value = 0  # 마지막 종목

        monitor.process_tick("015760", 45000)

        monitor._redis.delete.assert_any_call("forced_liquidation:armed")


class TestProfitFloor:
    def test_profit_floor_redis_operations(self):
        """profit_floor Redis get/set/delete."""
        monitor = _make_monitor()
        monitor._redis.get.return_value = None

        assert monitor._get_profit_floor("005930") is False

        monitor._set_profit_floor("005930")
        monitor._redis.setex.assert_called()

    def test_cleanup_includes_profit_floor(self):
        """_cleanup_position_state에서 profit_floor도 삭제."""
        monitor = _make_monitor()
        pipe_mock = MagicMock()
        monitor._redis.pipeline.return_value = pipe_mock

        monitor._cleanup_position_state("005930")

        # profit_floor: 삭제 호출 확인
        delete_calls = [str(c) for c in pipe_mock.delete.call_args_list]
        assert any("profit_floor:005930" in c for c in delete_calls)
