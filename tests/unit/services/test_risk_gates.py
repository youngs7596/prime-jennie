"""Risk Gates 단위 테스트."""

import time
from datetime import UTC, datetime

import fakeredis
import pytest

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
    check_overextension,
    check_rsi_guard,
    check_sell_cooldown,
    check_stoploss_cooldown,
    check_strategy_alignment,
    check_trade_tier,
)


@pytest.fixture()
def fake_redis():
    r = fakeredis.FakeRedis(version=(7,), decode_responses=True)
    yield r
    r.flushall()


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


class TestStoplossCooldown:
    def test_no_redis_passes(self):
        """Redis 없으면 통과."""
        result = check_stoploss_cooldown("005930", redis_client=None)
        assert result.passed

    def test_no_cooldown_passes(self, fake_redis):
        """쿨다운 키 없으면 통과."""
        result = check_stoploss_cooldown("005930", redis_client=fake_redis)
        assert result.passed

    def test_active_cooldown_fails(self, fake_redis):
        """쿨다운 키 있으면 차단."""
        fake_redis.setex("stoploss_cooldown:005930", 3 * 86400, "1")
        result = check_stoploss_cooldown("005930", redis_client=fake_redis)
        assert not result.passed
        assert result.gate_name == "stoploss_cooldown"
        assert "remaining" in result.reason

    def test_expired_cooldown_passes(self, fake_redis):
        """만료된 쿨다운은 통과."""
        fake_redis.setex("stoploss_cooldown:005930", 1, "1")
        import time as t

        t.sleep(1.1)
        result = check_stoploss_cooldown("005930", redis_client=fake_redis)
        assert result.passed


class TestSellCooldown:
    def test_no_redis_passes(self):
        """Redis 없으면 통과."""
        result = check_sell_cooldown("005930", redis_client=None)
        assert result.passed

    def test_no_cooldown_passes(self, fake_redis):
        """쿨다운 키 없으면 통과."""
        result = check_sell_cooldown("005930", redis_client=fake_redis)
        assert result.passed

    def test_active_cooldown_fails(self, fake_redis):
        """쿨다운 키 있으면 차단."""
        fake_redis.setex("sell_cooldown:005930", 86400, "1")
        result = check_sell_cooldown("005930", redis_client=fake_redis)
        assert not result.passed
        assert result.gate_name == "sell_cooldown"
        assert "remaining" in result.reason

    def test_expired_cooldown_passes(self, fake_redis):
        """만료된 쿨다운은 통과."""
        fake_redis.setex("sell_cooldown:005930", 1, "1")
        import time as t

        t.sleep(1.1)
        result = check_sell_cooldown("005930", redis_client=fake_redis)
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


class TestOverextension:
    """과열(Overextension) 필터 — 데이터마이닝 기반."""

    def test_none_disparity_passes(self):
        """이격률 데이터 없으면 통과."""
        result = check_overextension(None, MarketRegime.BULL)
        assert result.passed

    def test_bull_below_threshold_passes(self):
        """BULL 국면, 이격률 30% 미만 → 통과."""
        result = check_overextension(28.0, MarketRegime.BULL)
        assert result.passed

    def test_bull_above_threshold_fails(self):
        """BULL 국면, 이격률 30% 초과 → 차단."""
        result = check_overextension(31.5, MarketRegime.BULL)
        assert not result.passed
        assert "31.5%" in result.reason
        assert "30%" in result.reason

    def test_strong_bull_high_tolerance(self):
        """STRONG_BULL은 35%까지 허용."""
        assert check_overextension(34.0, MarketRegime.STRONG_BULL).passed
        assert not check_overextension(36.0, MarketRegime.STRONG_BULL).passed

    def test_sideways_moderate_threshold(self):
        """SIDEWAYS는 28% 기준 (Grid Search 최적화)."""
        assert check_overextension(27.0, MarketRegime.SIDEWAYS).passed
        assert not check_overextension(29.0, MarketRegime.SIDEWAYS).passed

    def test_bear_strict_threshold(self):
        """BEAR는 25% 기준."""
        assert check_overextension(24.0, MarketRegime.BEAR).passed
        assert not check_overextension(26.0, MarketRegime.BEAR).passed

    def test_strong_bear_strictest(self):
        """STRONG_BEAR는 20%로 가장 엄격."""
        assert check_overextension(19.0, MarketRegime.STRONG_BEAR).passed
        assert not check_overextension(21.0, MarketRegime.STRONG_BEAR).passed

    def test_negative_disparity_passes(self):
        """음수 이격률(하락 종목)은 항상 통과."""
        result = check_overextension(-5.0, MarketRegime.STRONG_BEAR)
        assert result.passed

    def test_gate_name(self):
        """게이트 이름 확인."""
        result = check_overextension(20.0, MarketRegime.BULL)
        assert result.gate_name == "overextension"


class TestStrategyAlignment:
    def test_avoid_strategy_blocked(self):
        """회피 전략은 차단."""
        ctx = _make_context(strategies_to_avoid=["MOMENTUM"])
        result = check_strategy_alignment("MOMENTUM", ctx)
        assert not result.passed
        assert "MOMENTUM" in result.reason

    def test_non_avoid_strategy_passes(self):
        """회피 목록에 없는 전략은 통과."""
        ctx = _make_context(strategies_to_avoid=["MOMENTUM"])
        result = check_strategy_alignment("GOLDEN_CROSS", ctx)
        assert result.passed

    def test_empty_avoid_list_passes(self):
        """빈 회피 목록은 모든 전략 통과."""
        ctx = _make_context(strategies_to_avoid=[])
        result = check_strategy_alignment("MOMENTUM", ctx)
        assert result.passed

    def test_default_context_passes(self):
        """기본 TradingContext (strategies_to_avoid 미지정)는 통과."""
        ctx = _make_context()
        result = check_strategy_alignment("MOMENTUM_CONTINUATION", ctx)
        assert result.passed

    def test_multiple_avoid_strategies(self):
        """여러 전략이 회피 목록에 있을 때."""
        ctx = _make_context(strategies_to_avoid=["MOMENTUM", "MOMENTUM_CONTINUATION", "DIP_BUY"])
        assert not check_strategy_alignment("MOMENTUM", ctx).passed
        assert not check_strategy_alignment("DIP_BUY", ctx).passed
        assert check_strategy_alignment("GOLDEN_CROSS", ctx).passed
