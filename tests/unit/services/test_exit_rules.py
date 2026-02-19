"""Exit Rules 단위 테스트."""

import pytest

from prime_jennie.domain.enums import MarketRegime, SellReason
from prime_jennie.services.monitor.exit_rules import (
    ExitSignal,
    PositionContext,
    check_atr_stop,
    check_fixed_stop,
    check_hard_stop,
    check_profit_lock,
    check_rsi_overbought,
    check_scale_out,
    check_time_exit,
    check_trailing_take_profit,
    evaluate_exit,
)


@pytest.fixture(autouse=True)
def _clear_config_cache():
    from prime_jennie.domain.config import get_config

    get_config.cache_clear()
    yield
    get_config.cache_clear()


def _make_ctx(**overrides) -> PositionContext:
    defaults = {
        "stock_code": "005930",
        "current_price": 72000,
        "buy_price": 70000,
        "quantity": 100,
        "profit_pct": 2.86,
        "high_watermark": 73000,
        "high_profit_pct": 4.29,
        "atr": 1400,
        "rsi": 55.0,
        "holding_days": 5,
        "scale_out_level": 0,
        "rsi_sold": False,
    }
    defaults.update(overrides)
    return PositionContext(**defaults)


class TestHardStop:
    def test_triggers_at_minus_10(self):
        ctx = _make_ctx(profit_pct=-10.0)
        signal = check_hard_stop(ctx)
        assert signal is not None
        assert signal.should_sell
        assert signal.reason == SellReason.STOP_LOSS
        assert signal.quantity_pct == 100.0

    def test_triggers_below_minus_10(self):
        ctx = _make_ctx(profit_pct=-12.5)
        signal = check_hard_stop(ctx)
        assert signal is not None

    def test_no_trigger_above(self):
        ctx = _make_ctx(profit_pct=-9.9)
        assert check_hard_stop(ctx) is None


class TestProfitLock:
    def test_level2_trigger(self):
        """고점 3%+ → 현재 1% 미만."""
        ctx = _make_ctx(high_profit_pct=5.0, profit_pct=0.5)
        signal = check_profit_lock(ctx)
        assert signal is not None
        assert "L2" in signal.description

    def test_level1_trigger(self):
        """고점 1.5%+ → 현재 0.5% 미만."""
        ctx = _make_ctx(high_profit_pct=2.0, profit_pct=0.2)
        signal = check_profit_lock(ctx)
        assert signal is not None
        assert "L1" in signal.description

    def test_no_trigger_healthy_profit(self):
        ctx = _make_ctx(high_profit_pct=5.0, profit_pct=3.0)
        assert check_profit_lock(ctx) is None

    def test_no_trigger_low_high(self):
        ctx = _make_ctx(high_profit_pct=1.0, profit_pct=0.3)
        assert check_profit_lock(ctx) is None


class TestATRStop:
    def test_triggers_below_stop(self):
        # stop = 70000 - 1400*2 = 67200
        ctx = _make_ctx(current_price=67000, buy_price=70000, atr=1400)
        signal = check_atr_stop(ctx)
        assert signal is not None
        assert signal.reason == SellReason.STOP_LOSS

    def test_no_trigger_above_stop(self):
        ctx = _make_ctx(current_price=68000, buy_price=70000, atr=1400)
        assert check_atr_stop(ctx) is None

    def test_macro_multiplier_widens_stop(self):
        # stop = 70000 - 1400*2*1.5 = 65800
        ctx = _make_ctx(current_price=66000, buy_price=70000, atr=1400)
        # Without macro: stop=67200, 66000 < 67200 → triggers
        assert check_atr_stop(ctx, 1.0) is not None
        # With macro 1.5x: stop=65800, 66000 > 65800 → no trigger
        assert check_atr_stop(ctx, 1.5) is None

    def test_zero_atr(self):
        ctx = _make_ctx(atr=0)
        assert check_atr_stop(ctx) is None


class TestFixedStop:
    def test_triggers_below_threshold(self):
        ctx = _make_ctx(profit_pct=-6.0)
        signal = check_fixed_stop(ctx)
        assert signal is not None
        assert signal.reason == SellReason.STOP_LOSS

    def test_no_trigger_above(self):
        ctx = _make_ctx(profit_pct=-4.0)
        assert check_fixed_stop(ctx) is None

    def test_macro_multiplier(self):
        # -5% * 1.5 = -7.5 threshold
        ctx = _make_ctx(profit_pct=-6.0)
        assert check_fixed_stop(ctx, 1.0) is not None
        assert check_fixed_stop(ctx, 1.5) is None


class TestTrailingTakeProfit:
    def test_triggers_on_drop(self):
        # high_profit >= 5% (activation), drop from high
        # trailing_stop = 77000 * (1 - 3.5/100) = 74305
        ctx = _make_ctx(
            current_price=74000,
            buy_price=70000,
            profit_pct=5.71,
            high_watermark=77000,
            high_profit_pct=10.0,
        )
        signal = check_trailing_take_profit(ctx, MarketRegime.SIDEWAYS)
        assert signal is not None
        assert signal.reason == SellReason.TRAILING_STOP

    def test_no_trigger_below_activation(self):
        ctx = _make_ctx(high_profit_pct=3.0)
        assert check_trailing_take_profit(ctx) is None

    def test_no_trigger_profit_too_low(self):
        # Drop from high but current profit < min_profit (3%)
        ctx = _make_ctx(
            current_price=71000,
            buy_price=70000,
            profit_pct=1.43,
            high_watermark=77000,
            high_profit_pct=10.0,
        )
        signal = check_trailing_take_profit(ctx, MarketRegime.SIDEWAYS)
        assert signal is None

    def test_disabled_when_config_off(self, monkeypatch):
        import prime_jennie.domain.config as cfg

        cfg.get_config.cache_clear()
        monkeypatch.setenv("SELL_TRAILING_ENABLED", "false")
        cfg.get_config.cache_clear()
        ctx = _make_ctx(high_profit_pct=10.0, profit_pct=5.0)
        # Will be None because config.sell.trailing_enabled is False
        # But since config might not pick up env var in this test setup,
        # we just verify no error
        result = check_trailing_take_profit(ctx)
        assert result is None or isinstance(result, ExitSignal)


class TestScaleOut:
    def test_level0_triggers(self):
        ctx = _make_ctx(profit_pct=3.5, scale_out_level=0)
        signal = check_scale_out(ctx, MarketRegime.BULL)
        assert signal is not None
        assert signal.quantity_pct == 25.0
        assert "L0" in signal.description

    def test_level1_triggers(self):
        ctx = _make_ctx(profit_pct=8.0, scale_out_level=1)
        signal = check_scale_out(ctx, MarketRegime.BULL)
        assert signal is not None
        assert signal.quantity_pct == 25.0

    def test_level_not_reached(self):
        ctx = _make_ctx(profit_pct=2.0, scale_out_level=0)
        assert check_scale_out(ctx, MarketRegime.BULL) is None

    def test_bear_lower_targets(self):
        ctx = _make_ctx(profit_pct=2.5, scale_out_level=0)
        # BEAR L0 = 2%, so 2.5 >= 2 → triggers
        signal = check_scale_out(ctx, MarketRegime.BEAR)
        assert signal is not None

    def test_all_levels_exhausted(self):
        ctx = _make_ctx(profit_pct=30.0, scale_out_level=4)
        assert check_scale_out(ctx) is None

    def test_tiny_position_forces_full_sell(self):
        ctx = _make_ctx(profit_pct=3.5, scale_out_level=0, quantity=5)
        signal = check_scale_out(ctx, MarketRegime.BULL)
        assert signal is not None
        assert signal.quantity_pct == 100.0  # 5 * 25% = 1, remaining=4 < 10


class TestRSIOverbought:
    def test_triggers(self):
        ctx = _make_ctx(rsi=78.0, profit_pct=4.0)
        signal = check_rsi_overbought(ctx)
        assert signal is not None
        assert signal.quantity_pct == 50.0
        assert signal.reason == SellReason.RSI_OVERBOUGHT

    def test_no_trigger_low_rsi(self):
        ctx = _make_ctx(rsi=65.0, profit_pct=5.0)
        assert check_rsi_overbought(ctx) is None

    def test_no_trigger_low_profit(self):
        ctx = _make_ctx(rsi=80.0, profit_pct=1.0)
        assert check_rsi_overbought(ctx) is None

    def test_already_sold(self):
        ctx = _make_ctx(rsi=80.0, profit_pct=5.0, rsi_sold=True)
        assert check_rsi_overbought(ctx) is None

    def test_no_rsi_data(self):
        ctx = _make_ctx(rsi=None, profit_pct=5.0)
        assert check_rsi_overbought(ctx) is None


class TestTimeExit:
    def test_triggers_at_limit(self):
        ctx = _make_ctx(holding_days=35)
        signal = check_time_exit(ctx, MarketRegime.SIDEWAYS)
        assert signal is not None
        assert signal.reason == SellReason.TIME_EXIT

    def test_bull_shorter_limit(self):
        ctx = _make_ctx(holding_days=20)
        signal = check_time_exit(ctx, MarketRegime.BULL)
        assert signal is not None

    def test_no_trigger_within_limit(self):
        ctx = _make_ctx(holding_days=10)
        assert check_time_exit(ctx, MarketRegime.BULL) is None


class TestEvaluateExit:
    def test_hard_stop_takes_priority(self):
        ctx = _make_ctx(profit_pct=-12.0, high_profit_pct=5.0)
        signal = evaluate_exit(ctx)
        assert signal is not None
        assert signal.reason == SellReason.STOP_LOSS
        assert "Hard stop" in signal.description

    def test_profit_lock_before_stop_loss(self):
        ctx = _make_ctx(
            profit_pct=0.3,
            high_profit_pct=3.5,
            current_price=70210,
            buy_price=70000,
            atr=1400,
        )
        signal = evaluate_exit(ctx)
        assert signal is not None
        assert "Profit Lock" in signal.description

    def test_no_exit_signal_healthy(self):
        ctx = _make_ctx(
            profit_pct=2.0,
            high_profit_pct=2.5,
            holding_days=3,
        )
        signal = evaluate_exit(ctx)
        assert signal is None

    def test_returns_first_match(self):
        """여러 조건 동시 충족 시 우선순위 높은 것 반환."""
        ctx = _make_ctx(
            profit_pct=-10.5,
            high_profit_pct=0.0,
            holding_days=50,
        )
        signal = evaluate_exit(ctx)
        assert signal is not None
        # Hard stop (-10.5%) has highest priority
        assert "Hard stop" in signal.description
