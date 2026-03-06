"""Intraday Risk Throttle — 장중 5단계 리스크 관리 테스트.

기존 circuit breaker 테스트를 대체.
"""

import json
import time
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from prime_jennie.domain.enums import MarketRegime, VixRegime
from prime_jennie.domain.macro import TradingContext


def _make_context(
    regime: MarketRegime = MarketRegime.BULL,
    position_multiplier: float = 1.0,
    council_multiplier_raw: float = 1.0,
    intraday_risk_level: str = "NORMAL",
    intraday_multiplier_raw: float = 1.0,
) -> TradingContext:
    return TradingContext(
        date=date.today(),
        market_regime=regime,
        position_multiplier=position_multiplier,
        stop_loss_multiplier=1.0,
        vix_regime=VixRegime.NORMAL,
        council_multiplier_raw=council_multiplier_raw,
        intraday_risk_level=intraday_risk_level,
        intraday_multiplier_raw=intraday_multiplier_raw,
    )


def _make_snapshot(kospi_chg: float = -0.5, vix: float = 20.0) -> str:
    return json.dumps(
        {
            "kospi_index": 2500,
            "kospi_change_pct": kospi_chg,
            "vix": vix,
            "vix_regime": "normal",
        }
    )


# ─── _calc_intraday_multiplier 단위 테스트 ────────────────────


class TestCalcIntradayMultiplier:
    def test_normal(self):
        from prime_jennie.services.jobs.app import _calc_intraday_multiplier

        assert _calc_intraday_multiplier(0.5, 20.0) == ("NORMAL", 1.0)
        assert _calc_intraday_multiplier(-0.5, 20.0) == ("NORMAL", 1.0)
        assert _calc_intraday_multiplier(-0.99, 29.9) == ("NORMAL", 1.0)

    def test_caution_by_kospi(self):
        from prime_jennie.services.jobs.app import _calc_intraday_multiplier

        assert _calc_intraday_multiplier(-1.0, 20.0) == ("CAUTION", 0.8)
        assert _calc_intraday_multiplier(-1.5, 20.0) == ("CAUTION", 0.8)

    def test_caution_by_vix(self):
        from prime_jennie.services.jobs.app import _calc_intraday_multiplier

        assert _calc_intraday_multiplier(-0.5, 30.0) == ("CAUTION", 0.8)
        assert _calc_intraday_multiplier(-0.5, 34.9) == ("CAUTION", 0.8)

    def test_warning_by_kospi(self):
        from prime_jennie.services.jobs.app import _calc_intraday_multiplier

        assert _calc_intraday_multiplier(-2.0, 20.0) == ("WARNING", 0.6)
        assert _calc_intraday_multiplier(-2.5, 20.0) == ("WARNING", 0.6)

    def test_warning_by_vix(self):
        from prime_jennie.services.jobs.app import _calc_intraday_multiplier

        assert _calc_intraday_multiplier(-0.5, 35.0) == ("WARNING", 0.6)
        assert _calc_intraday_multiplier(-0.5, 39.9) == ("WARNING", 0.6)

    def test_danger_by_kospi(self):
        from prime_jennie.services.jobs.app import _calc_intraday_multiplier

        assert _calc_intraday_multiplier(-3.0, 20.0) == ("DANGER", 0.3)
        assert _calc_intraday_multiplier(-3.5, 20.0) == ("DANGER", 0.3)

    def test_danger_by_vix(self):
        from prime_jennie.services.jobs.app import _calc_intraday_multiplier

        assert _calc_intraday_multiplier(-0.5, 40.0) == ("DANGER", 0.3)
        assert _calc_intraday_multiplier(-0.5, 50.0) == ("DANGER", 0.3)

    def test_critical(self):
        from prime_jennie.services.jobs.app import _calc_intraday_multiplier

        assert _calc_intraday_multiplier(-4.0, 20.0) == ("CRITICAL", 0.0)
        assert _calc_intraday_multiplier(-5.0, 50.0) == ("CRITICAL", 0.0)

    def test_critical_overrides_vix(self):
        """KOSPI -4% 이하는 VIX 무관 CRITICAL."""
        from prime_jennie.services.jobs.app import _calc_intraday_multiplier

        assert _calc_intraday_multiplier(-4.0, 10.0) == ("CRITICAL", 0.0)

    def test_boundary_exact_thresholds(self):
        """경계값 정확히 포함 확인 (<=)."""
        from prime_jennie.services.jobs.app import _calc_intraday_multiplier

        # -1.0 exactly → CAUTION
        assert _calc_intraday_multiplier(-1.0, 0.0)[0] == "CAUTION"
        # -2.0 exactly → WARNING
        assert _calc_intraday_multiplier(-2.0, 0.0)[0] == "WARNING"
        # -3.0 exactly → DANGER
        assert _calc_intraday_multiplier(-3.0, 0.0)[0] == "DANGER"
        # -4.0 exactly → CRITICAL
        assert _calc_intraday_multiplier(-4.0, 0.0)[0] == "CRITICAL"


# ─── _apply_recovery_logic 단위 테스트 ────────────────────────


class TestRecoveryLogic:
    def test_escalation_is_immediate(self):
        from prime_jennie.services.jobs.app import _apply_recovery_logic

        mock_redis = MagicMock()
        level, mult = _apply_recovery_logic(mock_redis, "NORMAL", "WARNING", 0.6)

        assert level == "WARNING"
        assert mult == 0.6
        mock_redis.delete.assert_called_once()

    def test_recovery_starts_timer(self):
        from prime_jennie.services.jobs.app import _apply_recovery_logic

        mock_redis = MagicMock()
        mock_redis.get.return_value = None  # no timer yet

        level, mult = _apply_recovery_logic(mock_redis, "WARNING", "NORMAL", 1.0)

        # Should hold at WARNING and start timer
        assert level == "WARNING"
        assert mult == 0.6
        mock_redis.set.assert_called_once()

    def test_recovery_too_early(self):
        from prime_jennie.services.jobs.app import _apply_recovery_logic

        mock_redis = MagicMock()
        # Timer started 1 minute ago (WARNING needs 2 cycles = 10min)
        mock_redis.get.return_value = str(time.time() - 60)

        level, mult = _apply_recovery_logic(mock_redis, "WARNING", "NORMAL", 1.0)

        assert level == "WARNING"
        assert mult == 0.6

    def test_recovery_after_enough_time(self):
        from prime_jennie.services.jobs.app import _apply_recovery_logic

        mock_redis = MagicMock()
        # Timer started 11 minutes ago (WARNING needs 2 cycles = 10min)
        mock_redis.get.return_value = str(time.time() - 660)

        level, mult = _apply_recovery_logic(mock_redis, "WARNING", "NORMAL", 1.0)

        # Should de-escalate one step: WARNING → CAUTION
        assert level == "CAUTION"
        assert mult == 0.8

    def test_recovery_never_goes_to_normal(self):
        """회복은 CAUTION까지만 — NORMAL은 Council에서만."""
        from prime_jennie.services.jobs.app import _apply_recovery_logic

        mock_redis = MagicMock()
        # Timer started long ago
        mock_redis.get.return_value = str(time.time() - 3600)

        level, mult = _apply_recovery_logic(mock_redis, "CAUTION", "NORMAL", 1.0)

        # CAUTION is the minimum, can't go to NORMAL
        assert level == "CAUTION"
        assert mult == 0.8

    def test_critical_recovery_to_danger(self):
        from prime_jennie.services.jobs.app import _apply_recovery_logic

        mock_redis = MagicMock()
        # CRITICAL needs 6 cycles = 30min
        mock_redis.get.return_value = str(time.time() - 1860)

        level, mult = _apply_recovery_logic(mock_redis, "CRITICAL", "NORMAL", 1.0)

        assert level == "DANGER"
        assert mult == 0.3

    def test_re_escalation_clears_timer(self):
        from prime_jennie.services.jobs.app import _apply_recovery_logic

        mock_redis = MagicMock()

        level, mult = _apply_recovery_logic(mock_redis, "CAUTION", "WARNING", 0.6)

        assert level == "WARNING"
        mock_redis.delete.assert_called_once()


# ─── min() 역전 방지 테스트 ────────────────────────────────────


class TestMinMultiplierLogic:
    def test_council_more_conservative(self):
        """Council 0.6 + Intraday CAUTION(0.8) → final 0.6 (Council wins)."""
        council = 0.6
        intraday = 0.8
        assert min(council, intraday) == 0.6

    def test_intraday_more_conservative(self):
        """Council 1.0 + Intraday WARNING(0.6) → final 0.6 (Intraday wins)."""
        council = 1.0
        intraday = 0.6
        assert min(council, intraday) == 0.6

    def test_both_same(self):
        """Council 0.8 + Intraday CAUTION(0.8) → final 0.8."""
        assert min(0.8, 0.8) == 0.8

    def test_intraday_never_overrides_council_upward(self):
        """Intraday NORMAL(1.0)이 Council 0.7을 절대 역전하지 않음."""
        assert min(0.7, 1.0) == 0.7


# ─── _check_intraday_risk 통합 테스트 ──────────────────────────


@pytest.fixture()
def mock_redis():
    r = MagicMock()
    return r


@pytest.fixture()
def mock_app_state(mock_redis):
    cache = MagicMock()
    with (
        patch("prime_jennie.services.jobs.app.get_redis", return_value=mock_redis),
        patch("prime_jennie.services.jobs.app.app") as mock_app,
        patch("prime_jennie.services.jobs.app._send_risk_alert"),
    ):
        mock_app.state.context_cache = cache
        yield mock_redis, cache


class TestCheckIntradayRisk:
    def test_no_trigger_on_mild_drop(self, mock_app_state):
        """KOSPI -0.5% — NORMAL 유지, context 변경 없음."""
        mock_redis, cache = mock_app_state
        mock_redis.get.return_value = _make_snapshot(kospi_chg=-0.5, vix=20.0)
        cache.get.return_value = _make_context(MarketRegime.BULL)

        from prime_jennie.services.jobs.app import _check_intraday_risk

        _check_intraday_risk()

        cache.set.assert_not_called()

    def test_kospi_2pct_triggers_warning(self, mock_app_state):
        """KOSPI -2.5% → WARNING, regime → BEAR."""
        mock_redis, cache = mock_app_state
        mock_redis.get.return_value = _make_snapshot(kospi_chg=-2.5, vix=20.0)
        cache.get.return_value = _make_context(MarketRegime.BULL)

        from prime_jennie.services.jobs.app import _check_intraday_risk

        _check_intraday_risk()

        cache.set.assert_called_once()
        ctx = cache.set.call_args[0][0]
        assert ctx.intraday_risk_level == "WARNING"
        assert ctx.market_regime == MarketRegime.BEAR
        assert ctx.is_high_volatility is True
        assert ctx.position_multiplier == 0.6

    def test_kospi_4pct_triggers_critical(self, mock_app_state):
        """KOSPI -4.5% → CRITICAL, regime → STRONG_BEAR, multiplier = 0."""
        mock_redis, cache = mock_app_state
        mock_redis.get.return_value = _make_snapshot(kospi_chg=-4.5, vix=25.0)
        cache.get.return_value = _make_context(MarketRegime.BULL)

        from prime_jennie.services.jobs.app import _check_intraday_risk

        _check_intraday_risk()

        cache.set.assert_called_once()
        ctx = cache.set.call_args[0][0]
        assert ctx.intraday_risk_level == "CRITICAL"
        assert ctx.market_regime == MarketRegime.STRONG_BEAR
        assert ctx.position_multiplier == 0.0

    def test_vix_35_triggers_warning(self, mock_app_state):
        """VIX >= 35, KOSPI 안정 → WARNING."""
        mock_redis, cache = mock_app_state
        mock_redis.get.return_value = _make_snapshot(kospi_chg=-0.5, vix=36.0)
        cache.get.return_value = _make_context(MarketRegime.STRONG_BULL)

        from prime_jennie.services.jobs.app import _check_intraday_risk

        _check_intraday_risk()

        cache.set.assert_called_once()
        ctx = cache.set.call_args[0][0]
        assert ctx.intraday_risk_level == "WARNING"
        assert ctx.market_regime == MarketRegime.BEAR

    def test_min_preserves_council_multiplier(self, mock_app_state):
        """Council 0.6 + Intraday CAUTION(0.8) → final 0.6."""
        mock_redis, cache = mock_app_state
        mock_redis.get.return_value = _make_snapshot(kospi_chg=-1.5, vix=20.0)
        cache.get.return_value = _make_context(
            MarketRegime.BEAR,
            position_multiplier=0.6,
            council_multiplier_raw=0.6,
        )

        from prime_jennie.services.jobs.app import _check_intraday_risk

        _check_intraday_risk()

        cache.set.assert_called_once()
        ctx = cache.set.call_args[0][0]
        assert ctx.intraday_risk_level == "CAUTION"
        assert ctx.intraday_multiplier_raw == 0.8
        assert ctx.position_multiplier == 0.6  # min(0.6, 0.8) = 0.6

    def test_no_upgrade_regime_on_recovery(self, mock_app_state):
        """이미 BEAR regime에서 CAUTION으로 회복해도 regime은 유지."""
        mock_redis, cache = mock_app_state
        mock_redis.get.return_value = _make_snapshot(kospi_chg=-1.0, vix=20.0)
        cache.get.return_value = _make_context(
            MarketRegime.BEAR,
            position_multiplier=0.6,
            council_multiplier_raw=0.6,
            intraday_risk_level="CAUTION",
            intraday_multiplier_raw=0.8,
        )

        from prime_jennie.services.jobs.app import _check_intraday_risk

        _check_intraday_risk()

        # CAUTION → CAUTION (same), no mult change → no update
        cache.set.assert_not_called()

    def test_no_snapshot_does_nothing(self, mock_app_state):
        mock_redis, cache = mock_app_state
        mock_redis.get.return_value = None

        from prime_jennie.services.jobs.app import _check_intraday_risk

        _check_intraday_risk()

        cache.get.assert_not_called()
        cache.set.assert_not_called()

    def test_no_context_does_nothing(self, mock_app_state):
        mock_redis, cache = mock_app_state
        mock_redis.get.return_value = _make_snapshot()
        cache.get.return_value = None

        from prime_jennie.services.jobs.app import _check_intraday_risk

        _check_intraday_risk()

        cache.set.assert_not_called()

    def test_backward_compat_infers_council_multiplier(self, mock_app_state):
        """이전 버전 context (council_multiplier_raw 미설정) 호환."""
        mock_redis, cache = mock_app_state
        mock_redis.get.return_value = _make_snapshot(kospi_chg=-1.5, vix=20.0)
        # council_multiplier_raw=1.0(default), position_multiplier=0.8(Council 값)
        cache.get.return_value = _make_context(
            MarketRegime.BULL,
            position_multiplier=0.8,
            council_multiplier_raw=1.0,
            intraday_risk_level="NORMAL",
            intraday_multiplier_raw=1.0,
        )

        from prime_jennie.services.jobs.app import _check_intraday_risk

        _check_intraday_risk()

        cache.set.assert_called_once()
        ctx = cache.set.call_args[0][0]
        # council_mult inferred as 0.8 (from position_multiplier)
        assert ctx.council_multiplier_raw == 0.8
        assert ctx.position_multiplier == 0.8  # min(0.8, 0.8)


# ─── TradingContext 모델 테스트 ────────────────────────────────


class TestTradingContextFields:
    def test_new_fields_have_defaults(self):
        ctx = TradingContext(
            date=date.today(),
            market_regime=MarketRegime.BULL,
        )
        assert ctx.intraday_risk_level == "NORMAL"
        assert ctx.intraday_multiplier_raw == 1.0
        assert ctx.council_multiplier_raw == 1.0

    def test_default_sets_council_raw(self):
        ctx = TradingContext.default()
        assert ctx.council_multiplier_raw == 0.8
        assert ctx.position_multiplier == 0.8
