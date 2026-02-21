"""Exit Rules 단위 테스트 — ATR 기반 profit lock, breakeven stop, time-tightening."""

import pytest

from prime_jennie.domain.enums import MarketRegime, SellReason
from prime_jennie.services.monitor.exit_rules import (
    ExitSignal,
    PositionContext,
    check_atr_stop,
    check_breakeven_stop,
    check_death_cross,
    check_fixed_stop,
    check_hard_stop,
    check_profit_floor,
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
        "macd_bearish": False,
        "death_cross": False,
        "profit_floor_active": False,
        "profit_floor_level": 10.0,
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


class TestProfitFloor:
    def test_triggers_when_active_below_floor(self):
        ctx = _make_ctx(
            profit_floor_active=True,
            profit_floor_level=10.0,
            profit_pct=8.0,
        )
        signal = check_profit_floor(ctx)
        assert signal is not None
        assert signal.reason == SellReason.PROFIT_FLOOR
        assert signal.quantity_pct == 100.0

    def test_no_trigger_when_inactive(self):
        ctx = _make_ctx(
            profit_floor_active=False,
            profit_pct=8.0,
        )
        assert check_profit_floor(ctx) is None

    def test_no_trigger_above_floor(self):
        ctx = _make_ctx(
            profit_floor_active=True,
            profit_floor_level=10.0,
            profit_pct=12.0,
        )
        assert check_profit_floor(ctx) is None


class TestProfitLock:
    """ATR 기반 동적 Profit Lock."""

    def test_level2_trigger(self):
        """ATR 기반 L2: atr_pct=2%, trigger=max(3.0, min(2*2.5, 5.0))=5.0%.
        high=6% >= 5% → profit < floor(1.0%) → 매도.
        """
        ctx = _make_ctx(
            buy_price=70000,
            atr=1400,  # 2% of buy_price
            high_profit_pct=6.0,
            profit_pct=0.5,
        )
        signal = check_profit_lock(ctx)
        assert signal is not None
        assert "L2" in signal.description

    def test_level1_trigger(self):
        """ATR 기반 L1: atr_pct=2%, trigger=max(1.5, min(2*1.5, 3.0))=3.0%.
        high=3.5% >= 3% → profit < floor(0.2%) → 매도.
        """
        ctx = _make_ctx(
            buy_price=70000,
            atr=1400,
            high_profit_pct=3.5,
            profit_pct=0.1,
        )
        signal = check_profit_lock(ctx)
        assert signal is not None
        assert "L1" in signal.description

    def test_no_trigger_healthy_profit(self):
        """수익이 floor 이상이면 매도 안 함."""
        ctx = _make_ctx(
            buy_price=70000,
            atr=1400,
            high_profit_pct=6.0,
            profit_pct=3.0,
        )
        assert check_profit_lock(ctx) is None

    def test_no_trigger_low_high(self):
        """고점이 trigger 미만이면 매도 안 함."""
        ctx = _make_ctx(
            buy_price=70000,
            atr=1400,
            high_profit_pct=1.0,
            profit_pct=0.1,
        )
        assert check_profit_lock(ctx) is None

    def test_no_trigger_zero_atr(self):
        """ATR=0이면 건너뜀."""
        ctx = _make_ctx(atr=0, high_profit_pct=5.0, profit_pct=0.0)
        assert check_profit_lock(ctx) is None

    def test_dynamic_trigger_with_small_atr(self):
        """ATR이 작으면 floor(min) 값이 trigger."""
        ctx = _make_ctx(
            buy_price=70000,
            atr=350,  # 0.5% → L1 trigger = max(1.5, min(0.75, 3.0)) = 1.5%
            high_profit_pct=2.0,  # >= 1.5%
            profit_pct=0.1,  # < 0.2% floor
        )
        signal = check_profit_lock(ctx)
        assert signal is not None
        assert "L1" in signal.description


class TestBreakevenStop:
    """Breakeven Stop: +3% 도달 후 floor(+0.3%) 미만 → 전량 매도."""

    def test_triggers_when_high_reached_and_below_floor(self):
        """고점 3.5%, 현재 0.2% → breakeven floor(0.3%) 미만 → 매도."""
        ctx = _make_ctx(high_profit_pct=3.5, profit_pct=0.2)
        signal = check_breakeven_stop(ctx)
        assert signal is not None
        assert signal.should_sell
        assert signal.reason == SellReason.BREAKEVEN_STOP
        assert signal.quantity_pct == 100.0
        assert "Breakeven stop" in signal.description

    def test_triggers_when_profit_negative(self):
        """고점 4%, 현재 -1% → 마이너스도 캐치."""
        ctx = _make_ctx(high_profit_pct=4.0, profit_pct=-1.0)
        signal = check_breakeven_stop(ctx)
        assert signal is not None
        assert "Breakeven stop" in signal.description

    def test_no_trigger_above_floor(self):
        """고점 3.5%, 현재 0.5% → floor(0.3%) 이상 → 매도 안 함."""
        ctx = _make_ctx(high_profit_pct=3.5, profit_pct=0.5)
        assert check_breakeven_stop(ctx) is None

    def test_no_trigger_high_not_reached(self):
        """고점 2% → activation(3%) 미달 → 매도 안 함."""
        ctx = _make_ctx(high_profit_pct=2.0, profit_pct=-1.0)
        assert check_breakeven_stop(ctx) is None

    def test_disabled_when_config_off(self, monkeypatch):
        import prime_jennie.domain.config as cfg

        cfg.get_config.cache_clear()
        monkeypatch.setenv("SELL_BREAKEVEN_ENABLED", "false")
        cfg.get_config.cache_clear()
        ctx = _make_ctx(high_profit_pct=5.0, profit_pct=-1.0)
        assert check_breakeven_stop(ctx) is None

    def test_profit_lock_catches_before_breakeven(self):
        """Profit Lock L1이 먼저 체크되므로, floor 이상이면 Lock이 잡음."""
        # L1 trigger=3.0%, high=3.5% >= 3.0%, profit=0.1% < L1 floor(0.2%)
        # → Profit Lock L1이 먼저 발동
        ctx = _make_ctx(buy_price=70000, atr=1400, high_profit_pct=3.5, profit_pct=0.1)
        signal = evaluate_exit(ctx)
        assert signal is not None
        assert "Profit Lock" in signal.description


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
        assert check_atr_stop(ctx, 1.0) is not None
        assert check_atr_stop(ctx, 1.5) is None

    def test_zero_atr(self):
        ctx = _make_ctx(atr=0)
        assert check_atr_stop(ctx) is None

    def test_macd_bearish_tightens_stop(self):
        """MACD bearish → mult ×0.75 (스톱 타이트닝)."""
        # normal stop = 70000 - 1400*2 = 67200
        # macd stop = 70000 - 1400*2*0.75 = 67900
        ctx = _make_ctx(
            current_price=67500,
            buy_price=70000,
            atr=1400,
            macd_bearish=True,
        )
        # 67500 < 67900 → triggers with MACD
        assert check_atr_stop(ctx) is not None
        # Without MACD: 67500 > 67200 → no trigger
        ctx_no_macd = _make_ctx(current_price=67500, buy_price=70000, atr=1400)
        assert check_atr_stop(ctx_no_macd) is None

    def test_death_cross_tightens_stop(self):
        """Death cross → mult ×0.8."""
        # normal stop = 70000 - 1400*2 = 67200
        # dc stop = 70000 - 1400*2*0.8 = 67760
        ctx = _make_ctx(
            current_price=67400,
            buy_price=70000,
            atr=1400,
            death_cross=True,
        )
        assert check_atr_stop(ctx) is not None


class TestFixedStop:
    def test_triggers_below_threshold(self):
        ctx = _make_ctx(profit_pct=-6.0)
        signal = check_fixed_stop(ctx)
        assert signal is not None
        assert signal.reason == SellReason.STOP_LOSS

    def test_no_trigger_above(self):
        ctx = _make_ctx(profit_pct=-5.0)
        assert check_fixed_stop(ctx) is None

    def test_macro_multiplier(self):
        # -6% * 1.5 = -9.0 threshold
        ctx = _make_ctx(profit_pct=-7.0)
        assert check_fixed_stop(ctx, 1.0) is not None
        assert check_fixed_stop(ctx, 1.5) is None

    def test_time_tighten_day20(self):
        """20일 보유: threshold = -6% + 2.0*10/20 = -5.0%.
        profit=-5.5% <= -5.0% → 매도.
        """
        ctx = _make_ctx(profit_pct=-5.5, holding_days=20)
        signal = check_fixed_stop(ctx)
        assert signal is not None
        assert "day 20" in signal.description

    def test_time_tighten_day30_max(self):
        """30일 보유(max): threshold = -6% + 2.0 = -4.0%.
        profit=-4.5% <= -4.0% → 매도.
        """
        ctx = _make_ctx(profit_pct=-4.5, holding_days=30)
        signal = check_fixed_stop(ctx)
        assert signal is not None

    def test_time_tighten_no_effect_before_start(self):
        """10일 이하: tightening 미적용. threshold=-6.0%.
        profit=-5.5% > -6.0% → 매도 안 함.
        """
        ctx = _make_ctx(profit_pct=-5.5, holding_days=10)
        assert check_fixed_stop(ctx) is None

    def test_time_tighten_disabled(self, monkeypatch):
        """비활성화 시 기존 동작 유지."""
        import prime_jennie.domain.config as cfg

        cfg.get_config.cache_clear()
        monkeypatch.setenv("SELL_TIME_TIGHTEN_ENABLED", "false")
        cfg.get_config.cache_clear()
        # 20일 보유지만 tightening 비활성 → threshold=-6.0%
        ctx = _make_ctx(profit_pct=-5.5, holding_days=20)
        assert check_fixed_stop(ctx) is None

    def test_time_tighten_bull_starts_later(self):
        """BULL 국면: 15일부터 시작 (기본 10일보다 5일 늦음).
        12일 보유, SIDEWAYS: threshold=-6+0.2=-5.8%, profit=-5.9% → 매도.
        12일 보유, BULL: threshold=-6.0% (미적용), profit=-5.9% → 매도 안 함.
        """
        ctx = _make_ctx(profit_pct=-5.9, holding_days=12)
        # SIDEWAYS: 12일 > 10일 → tighten=2.0*2/20=0.2 → threshold=-5.8% → 매도
        signal_sw = check_fixed_stop(ctx, regime=MarketRegime.SIDEWAYS)
        assert signal_sw is not None
        # BULL: 12일 <= 15일 → tightening 미적용, threshold=-6.0% → 매도 안 함
        signal_bull = check_fixed_stop(ctx, regime=MarketRegime.BULL)
        assert signal_bull is None

    def test_time_tighten_bull_day20(self):
        """BULL 20일: threshold = -6% + 2.0*5/15 = -5.33%.
        profit=-5.5% <= -5.33% → 매도.
        """
        ctx = _make_ctx(profit_pct=-5.5, holding_days=20)
        signal = check_fixed_stop(ctx, regime=MarketRegime.BULL)
        assert signal is not None


class TestTrailingTakeProfit:
    def test_triggers_on_drop(self):
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
        ctx = _make_ctx(
            current_price=71000,
            buy_price=70000,
            profit_pct=1.43,
            high_watermark=77000,
            high_profit_pct=10.0,
        )
        assert check_trailing_take_profit(ctx, MarketRegime.SIDEWAYS) is None

    def test_macd_lowers_activation(self):
        """MACD bearish → activation ×0.8 (4% instead of 5%)."""
        ctx = _make_ctx(
            high_profit_pct=4.5,  # >= 4% (5*0.8) but < 5%
            macd_bearish=True,
            current_price=74000,
            buy_price=70000,
            profit_pct=5.71,
            high_watermark=77000,
        )
        signal = check_trailing_take_profit(ctx, MarketRegime.SIDEWAYS)
        assert signal is not None

    def test_death_cross_lowers_activation(self):
        """Death cross → activation ×0.7 (3.5% instead of 5%)."""
        ctx = _make_ctx(
            high_profit_pct=3.8,  # >= 3.5% (5*0.7) but < 4%
            death_cross=True,
            current_price=74000,
            buy_price=70000,
            profit_pct=5.71,
            high_watermark=77000,
        )
        signal = check_trailing_take_profit(ctx, MarketRegime.SIDEWAYS)
        assert signal is not None

    def test_disabled_when_config_off(self, monkeypatch):
        import prime_jennie.domain.config as cfg

        cfg.get_config.cache_clear()
        monkeypatch.setenv("SELL_TRAILING_ENABLED", "false")
        cfg.get_config.cache_clear()
        ctx = _make_ctx(high_profit_pct=10.0, profit_pct=5.0)
        result = check_trailing_take_profit(ctx)
        assert result is None or isinstance(result, ExitSignal)


class TestScaleOut:
    def test_level0_triggers(self):
        ctx = _make_ctx(profit_pct=3.5, scale_out_level=0, quantity=200)
        signal = check_scale_out(ctx, MarketRegime.BULL)
        assert signal is not None
        assert signal.quantity_pct == 25.0
        assert "L0" in signal.description

    def test_level_not_reached(self):
        ctx = _make_ctx(profit_pct=2.0, scale_out_level=0)
        assert check_scale_out(ctx, MarketRegime.BULL) is None

    def test_bear_lower_targets(self):
        ctx = _make_ctx(profit_pct=2.5, scale_out_level=0, quantity=200)
        signal = check_scale_out(ctx, MarketRegime.BEAR)
        assert signal is not None

    def test_all_levels_exhausted(self):
        ctx = _make_ctx(profit_pct=30.0, scale_out_level=4)
        assert check_scale_out(ctx) is None

    def test_min_transaction_guard_skips(self):
        """매도 금액 < 50만원 & 수량 < 50주 → 스킵 (총 포지션이 충분히 큰 경우)."""
        # 200주 * 25% = 50주, 50주 * 5000원 = 250,000 < 500,000
        # total = 200 * 5000 = 1,000,000 >= 500,000*2 → 전량 전환 안 함 → 스킵
        ctx = _make_ctx(
            profit_pct=3.5,
            scale_out_level=0,
            quantity=200,
            current_price=5000,
        )
        signal = check_scale_out(ctx, MarketRegime.BULL)
        assert signal is None  # 최소 거래 미달

    def test_min_transaction_guard_converts_to_full_sell(self):
        """매도 금액 < 50만원 & 총 포지션도 작으면 → 전량 전환."""
        # 100주 * 25% = 25주, 25주 * 5000원 = 125,000 < 500,000
        # total = 100 * 5000 = 500,000 < 1,000,000 → 전량 전환
        ctx = _make_ctx(
            profit_pct=3.5,
            scale_out_level=0,
            quantity=100,
            current_price=5000,
        )
        signal = check_scale_out(ctx, MarketRegime.BULL)
        assert signal is not None
        assert signal.quantity_pct == 100.0

    def test_min_quantity_forces_full_sell(self):
        """잔량 < min_sell_quantity(50) & 총 포지션 작으면 → 전량."""
        # 60주 * 25% = 15주, 15 < 50 (min_sell_quantity)
        # total = 60 * 10000 = 600,000 < 1,000,000 (min_transaction * 2) → 전량 전환
        ctx = _make_ctx(
            profit_pct=3.5,
            scale_out_level=0,
            quantity=60,
            current_price=10000,
        )
        signal = check_scale_out(ctx, MarketRegime.BULL)
        assert signal is not None
        assert signal.quantity_pct == 100.0


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


class TestDeathCross:
    def test_triggers_on_loss(self):
        ctx = _make_ctx(death_cross=True, profit_pct=-2.0)
        signal = check_death_cross(ctx)
        assert signal is not None
        assert signal.reason == SellReason.DEATH_CROSS
        assert signal.quantity_pct == 100.0

    def test_no_trigger_in_profit(self):
        ctx = _make_ctx(death_cross=True, profit_pct=1.0)
        assert check_death_cross(ctx) is None

    def test_no_trigger_without_flag(self):
        ctx = _make_ctx(death_cross=False, profit_pct=-2.0)
        assert check_death_cross(ctx) is None


class TestTimeExit:
    def test_triggers_at_limit(self):
        ctx = _make_ctx(holding_days=30)
        signal = check_time_exit(ctx, MarketRegime.SIDEWAYS)
        assert signal is not None
        assert signal.reason == SellReason.TIME_EXIT

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

    def test_profit_floor_before_profit_lock(self):
        ctx = _make_ctx(
            profit_floor_active=True,
            profit_floor_level=10.0,
            profit_pct=8.0,
            high_profit_pct=16.0,
        )
        signal = evaluate_exit(ctx)
        assert signal is not None
        assert signal.reason == SellReason.PROFIT_FLOOR

    def test_profit_lock_before_stop_loss(self):
        ctx = _make_ctx(
            profit_pct=0.1,
            high_profit_pct=3.5,
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

    def test_breakeven_catches_after_profit_lock(self):
        """Profit Lock에 안 걸리지만 Breakeven에 걸리는 케이스.

        high=3.5%: L1 trigger=3.0% 활성. profit=0.25% → L1 floor(0.2%) 이상이므로 Lock 미발동.
        하지만 breakeven floor(0.3%) 미만이므로 Breakeven 발동.
        """
        ctx = _make_ctx(
            buy_price=70000,
            atr=1400,
            high_profit_pct=3.5,
            profit_pct=0.25,
        )
        signal = evaluate_exit(ctx)
        assert signal is not None
        assert "Breakeven stop" in signal.description

    def test_returns_first_match(self):
        """여러 조건 동시 충족 시 우선순위 높은 것 반환."""
        ctx = _make_ctx(
            profit_pct=-10.5,
            high_profit_pct=0.0,
            holding_days=50,
        )
        signal = evaluate_exit(ctx)
        assert signal is not None
        assert "Hard stop" in signal.description
