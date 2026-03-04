"""Circuit Breaker — 장중 자동 regime downgrade 테스트."""

import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from prime_jennie.domain.enums import MarketRegime, VixRegime
from prime_jennie.domain.macro import TradingContext


def _make_context(regime: MarketRegime = MarketRegime.BULL) -> TradingContext:
    return TradingContext(
        date=date.today(),
        market_regime=regime,
        position_multiplier=1.0,
        stop_loss_multiplier=1.0,
        vix_regime=VixRegime.NORMAL,
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


@pytest.fixture()
def mock_redis():
    r = MagicMock()
    return r


@pytest.fixture()
def mock_app_state(mock_redis):
    """Patch get_redis and app.state.context_cache."""
    cache = MagicMock()
    with (
        patch("prime_jennie.services.jobs.app.get_redis", return_value=mock_redis),
        patch("prime_jennie.services.jobs.app.app") as mock_app,
    ):
        mock_app.state.context_cache = cache
        yield mock_redis, cache


class TestCircuitBreaker:
    """_check_circuit_breaker 로직 검증."""

    def test_no_trigger_on_mild_drop(self, mock_app_state):
        """KOSPI -1% — circuit breaker 미작동."""
        mock_redis, cache = mock_app_state
        mock_redis.get.return_value = _make_snapshot(kospi_chg=-1.0, vix=20.0)
        cache.get.return_value = _make_context(MarketRegime.BULL)

        from prime_jennie.services.jobs.app import _check_circuit_breaker

        _check_circuit_breaker()

        cache.set.assert_not_called()

    def test_kospi_drop_2pct_downgrades_to_bear(self, mock_app_state):
        """KOSPI -2% — BULL → BEAR downgrade."""
        mock_redis, cache = mock_app_state
        mock_redis.get.return_value = _make_snapshot(kospi_chg=-2.5, vix=20.0)
        cache.get.return_value = _make_context(MarketRegime.BULL)

        from prime_jennie.services.jobs.app import _check_circuit_breaker

        _check_circuit_breaker()

        cache.set.assert_called_once()
        ctx = cache.set.call_args[0][0]
        assert ctx.market_regime == MarketRegime.BEAR
        assert ctx.is_high_volatility is True
        assert ctx.position_multiplier <= 0.6

    def test_kospi_drop_4pct_downgrades_to_strong_bear(self, mock_app_state):
        """KOSPI -4% — BULL → STRONG_BEAR."""
        mock_redis, cache = mock_app_state
        mock_redis.get.return_value = _make_snapshot(kospi_chg=-4.5, vix=25.0)
        cache.get.return_value = _make_context(MarketRegime.BULL)

        from prime_jennie.services.jobs.app import _check_circuit_breaker

        _check_circuit_breaker()

        cache.set.assert_called_once()
        ctx = cache.set.call_args[0][0]
        assert ctx.market_regime == MarketRegime.STRONG_BEAR

    def test_vix_crisis_downgrades_to_bear(self, mock_app_state):
        """VIX ≥ 35 — STRONG_BULL → BEAR."""
        mock_redis, cache = mock_app_state
        mock_redis.get.return_value = _make_snapshot(kospi_chg=-0.5, vix=36.0)
        cache.get.return_value = _make_context(MarketRegime.STRONG_BULL)

        from prime_jennie.services.jobs.app import _check_circuit_breaker

        _check_circuit_breaker()

        cache.set.assert_called_once()
        ctx = cache.set.call_args[0][0]
        assert ctx.market_regime == MarketRegime.BEAR

    def test_no_upgrade_if_already_bear(self, mock_app_state):
        """이미 BEAR — downgrade만 허용, 동일 regime은 스킵."""
        mock_redis, cache = mock_app_state
        mock_redis.get.return_value = _make_snapshot(kospi_chg=-2.5, vix=30.0)
        cache.get.return_value = _make_context(MarketRegime.BEAR)

        from prime_jennie.services.jobs.app import _check_circuit_breaker

        _check_circuit_breaker()

        cache.set.assert_not_called()

    def test_strong_bear_override_from_sideways(self, mock_app_state):
        """KOSPI -5% — SIDEWAYS → STRONG_BEAR."""
        mock_redis, cache = mock_app_state
        mock_redis.get.return_value = _make_snapshot(kospi_chg=-5.0, vix=28.0)
        cache.get.return_value = _make_context(MarketRegime.SIDEWAYS)

        from prime_jennie.services.jobs.app import _check_circuit_breaker

        _check_circuit_breaker()

        cache.set.assert_called_once()
        ctx = cache.set.call_args[0][0]
        assert ctx.market_regime == MarketRegime.STRONG_BEAR

    def test_no_snapshot_does_nothing(self, mock_app_state):
        """Redis에 snapshot 없으면 무시."""
        mock_redis, cache = mock_app_state
        mock_redis.get.return_value = None

        from prime_jennie.services.jobs.app import _check_circuit_breaker

        _check_circuit_breaker()

        cache.get.assert_not_called()
        cache.set.assert_not_called()

    def test_no_context_does_nothing(self, mock_app_state):
        """TradingContext 없으면 무시."""
        mock_redis, cache = mock_app_state
        mock_redis.get.return_value = _make_snapshot()
        cache.get.return_value = None

        from prime_jennie.services.jobs.app import _check_circuit_breaker

        _check_circuit_breaker()

        cache.set.assert_not_called()
