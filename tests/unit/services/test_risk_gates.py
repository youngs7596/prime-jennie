"""Risk Gates 단위 테스트."""

import time
from datetime import UTC, datetime

from prime_jennie.domain.config import ScannerConfig
from prime_jennie.domain.enums import MarketRegime, TradeTier, VixRegime
from prime_jennie.domain.macro import TradingContext
from prime_jennie.services.scanner.bar_engine import Bar
from prime_jennie.services.scanner.risk_gates import (
    check_combined_risk,
    check_cooldown,
    check_danger_zone,
    check_macro_risk,
    check_market_regime,
    check_micro_timing,
    check_min_bars,
    check_no_trade_window,
    check_rsi_guard,
    check_trade_tier,
)


def _make_bar(close: float = 100, open: float = 99, high: float = 101, low: float = 98, volume: int = 1000) -> Bar:
    return Bar(timestamp=1000.0, open=open, high=high, low=low, close=close, volume=volume)


def _make_bars(n: int = 20) -> list[Bar]:
    return [_make_bar(close=100 + i * 0.1) for i in range(n)]


def _make_config(**overrides) -> ScannerConfig:
    defaults = {
        "min_required_bars": 20,
        "signal_cooldown_seconds": 600,
        "rsi_guard_max": 75.0,
        "volume_ratio_warning": 2.0,
        "vwap_deviation_warning": 0.02,
        "no_trade_window_start": "09:00",
        "no_trade_window_end": "09:15",
        "danger_zone_start": "14:00",
        "danger_zone_end": "15:00",
    }
    defaults.update(overrides)
    return ScannerConfig(**defaults)


def _make_context(regime=MarketRegime.BULL, **kwargs) -> TradingContext:
    from datetime import date

    return TradingContext(date=date(2026, 2, 19), market_regime=regime, **kwargs)


class TestMinBars:
    def test_enough_bars_passes(self):
        result = check_min_bars(_make_bars(20), min_required=20)
        assert result.passed

    def test_insufficient_bars_fails(self):
        result = check_min_bars(_make_bars(5), min_required=20)
        assert not result.passed
        assert "Need 20" in result.reason


class TestNoTradeWindow:
    def test_inside_window_fails(self):
        config = _make_config()
        now = datetime(2026, 2, 19, 9, 10, tzinfo=UTC)  # mock as KST
        result = check_no_trade_window(config, now)
        assert not result.passed

    def test_outside_window_passes(self):
        config = _make_config()
        now = datetime(2026, 2, 19, 10, 0, tzinfo=UTC)
        result = check_no_trade_window(config, now)
        assert result.passed


class TestDangerZone:
    def test_inside_zone_fails(self):
        config = _make_config()
        now = datetime(2026, 2, 19, 14, 30, tzinfo=UTC)
        result = check_danger_zone(config, now)
        assert not result.passed

    def test_outside_zone_passes(self):
        config = _make_config()
        now = datetime(2026, 2, 19, 11, 0, tzinfo=UTC)
        result = check_danger_zone(config, now)
        assert result.passed


class TestRSIGuard:
    def test_rsi_below_max_passes(self):
        result = check_rsi_guard(60.0, max_rsi=75.0)
        assert result.passed

    def test_rsi_above_max_fails(self):
        result = check_rsi_guard(80.0, max_rsi=75.0)
        assert not result.passed

    def test_rsi_none_passes(self):
        result = check_rsi_guard(None)
        assert result.passed


class TestMacroRisk:
    def test_low_risk_passes(self):
        ctx = _make_context(risk_off_level=0)
        assert check_macro_risk(ctx).passed

    def test_risk_off_level_2_fails(self):
        ctx = _make_context(risk_off_level=2)
        assert not check_macro_risk(ctx).passed

    def test_vix_crisis_fails(self):
        ctx = _make_context(vix_regime=VixRegime.CRISIS)
        assert not check_macro_risk(ctx).passed


class TestMarketRegime:
    def test_bull_passes(self):
        assert check_market_regime(MarketRegime.BULL).passed

    def test_bear_fails(self):
        assert not check_market_regime(MarketRegime.BEAR).passed

    def test_bear_with_block_disabled_passes(self):
        assert check_market_regime(MarketRegime.BEAR, block_bear=False).passed


class TestCombinedRisk:
    def test_no_risk_passes(self):
        result = check_combined_risk(1.0, 100.0, 100.0)
        assert result.passed

    def test_volume_only_passes(self):
        result = check_combined_risk(3.0, 100.0, 100.0)
        assert result.passed

    def test_both_risks_fails(self):
        # volume > 2x AND price > VWAP * 1.02
        result = check_combined_risk(3.0, 100.0, 105.0)
        assert not result.passed


class TestCooldown:
    def test_no_previous_signal_passes(self):
        result = check_cooldown("005930", {}, 600)
        assert result.passed

    def test_recent_signal_fails(self):
        last = {
            "005930": time.time() - 100  # 100초 전
        }
        result = check_cooldown("005930", last, 600)
        assert not result.passed

    def test_old_signal_passes(self):
        last = {
            "005930": time.time() - 700  # 700초 전 (> 600)
        }
        result = check_cooldown("005930", last, 600)
        assert result.passed


class TestTradeTier:
    def test_tier1_passes(self):
        assert check_trade_tier(TradeTier.TIER1).passed

    def test_blocked_fails(self):
        assert not check_trade_tier(TradeTier.BLOCKED).passed


class TestMicroTiming:
    def test_normal_bars_pass(self):
        bars = [_make_bar(open=100, close=101, high=102, low=99)]
        bars.append(_make_bar(open=101, close=102, high=103, low=100))
        assert check_micro_timing(bars).passed

    def test_shooting_star_fails(self):
        """긴 윗꼬리 + 작은 몸통."""
        bar1 = _make_bar(open=100, close=101, high=102, low=99)
        bar2 = _make_bar(open=100, close=100.5, high=105, low=99.5)
        # upper shadow = 105 - 100.5 = 4.5, body = 0.5 → 4.5 > 0.5*2
        result = check_micro_timing([bar1, bar2])
        assert not result.passed
        assert "Shooting Star" in result.reason

    def test_bearish_engulfing_fails(self):
        """이전 양봉을 감싸는 음봉."""
        bar1 = _make_bar(open=100, close=101, high=102, low=99)
        bar2 = _make_bar(open=102, close=99, high=103, low=98)  # 감싸기
        result = check_micro_timing([bar1, bar2])
        assert not result.passed
        assert "Bearish Engulfing" in result.reason

    def test_insufficient_bars_passes(self):
        result = check_micro_timing([_make_bar()])
        assert result.passed
