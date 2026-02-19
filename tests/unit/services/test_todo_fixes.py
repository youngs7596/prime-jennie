"""TODO stub 수정 검증 테스트.

- RSI 계산 (calculate_rsi)
- ATR 계산 (calculate_atr) — 기존 + 일봉 기반 연동
- BuySignal sector_group 전달
- PriceMonitor RSI 연동
- BuyExecutor ATR/sector_group 연동
"""

from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from prime_jennie.services.buyer.position_sizing import calculate_atr, calculate_rsi, clamp_atr


# ─── RSI Calculation ───────────────────────────────────────────


class TestCalculateRSI:
    """14-period RSI 계산."""

    def test_insufficient_data_returns_none(self):
        """14+1개 미만 → None."""
        assert calculate_rsi([100.0] * 14) is None
        assert calculate_rsi([]) is None

    def test_exactly_15_prices(self):
        """최소 데이터(15개)로 RSI 계산."""
        prices = [float(100 + i) for i in range(15)]  # 순 상승
        rsi = calculate_rsi(prices)
        assert rsi is not None
        assert rsi == 100.0  # 전부 상승이면 RSI=100

    def test_all_declining(self):
        """전부 하락이면 RSI=0."""
        prices = [float(100 - i) for i in range(15)]
        rsi = calculate_rsi(prices)
        assert rsi is not None
        assert rsi == 0.0

    def test_mixed_prices_in_range(self):
        """일반적인 등락 → 0~100 사이."""
        prices = [100, 102, 101, 103, 100, 98, 101, 104, 103, 105,
                  102, 99, 101, 103, 104, 106, 103, 101, 104, 107]
        rsi = calculate_rsi([float(p) for p in prices])
        assert rsi is not None
        assert 0.0 < rsi < 100.0

    def test_30_prices_wilder_smoothing(self):
        """30개 데이터 — Wilder's smoothing 검증."""
        prices = [float(100 + (i % 3) - 1) for i in range(30)]
        rsi = calculate_rsi(prices)
        assert rsi is not None
        assert 30.0 < rsi < 70.0  # 등락 반복 → 중간 RSI


# ─── ATR Calculation ───────────────────────────────────────────


class TestCalculateATR:
    """True Range 기반 ATR."""

    def test_insufficient_data(self):
        assert calculate_atr([]) == 0.0
        assert calculate_atr([{"high": 100, "low": 90, "close": 95}]) == 0.0

    def test_simple_atr(self):
        """2개 봉: TR = max(H-L, |H-prevC|, |L-prevC|)."""
        prices = [
            {"high": 100, "low": 90, "close": 95},
            {"high": 105, "low": 92, "close": 100},
        ]
        atr = calculate_atr(prices)
        # TR = max(105-92, |105-95|, |92-95|) = max(13, 10, 3) = 13
        assert atr == 13.0

    def test_14_period_average(self):
        """14봉 이상 → 최근 14개 TR 평균."""
        prices = [{"high": 100 + i, "low": 90 + i, "close": 95 + i} for i in range(20)]
        atr = calculate_atr(prices, period=14)
        assert atr > 0


# ─── BuySignal sector_group ───────────────────────────────────


class TestBuySignalSectorGroup:
    """BuySignal에 sector_group 필드 추가 검증."""

    def test_sector_group_field_exists(self):
        from prime_jennie.domain.enums import SectorGroup
        from prime_jennie.domain.trading import BuySignal

        signal = BuySignal(
            stock_code="005930",
            stock_name="삼성전자",
            signal_type="MOMENTUM",
            signal_price=70000,
            llm_score=75.0,
            hybrid_score=72.0,
            trade_tier="TIER1",
            sector_group=SectorGroup.SEMICONDUCTOR_IT,
            market_regime="BULL",
            timestamp=datetime.now(timezone.utc),
        )
        assert signal.sector_group == SectorGroup.SEMICONDUCTOR_IT

    def test_sector_group_defaults_none(self):
        from prime_jennie.domain.trading import BuySignal

        signal = BuySignal(
            stock_code="005930",
            stock_name="삼성전자",
            signal_type="MOMENTUM",
            signal_price=70000,
            llm_score=75.0,
            hybrid_score=72.0,
            trade_tier="TIER1",
            market_regime="BULL",
            timestamp=datetime.now(timezone.utc),
        )
        assert signal.sector_group is None


# ─── KISClient.get_daily_prices ───────────────────────────────


class TestKISClientDailyPrices:
    """KISClient.get_daily_prices() 메서드."""

    def test_get_daily_prices_method_exists(self):
        from prime_jennie.infra.kis.client import KISClient

        assert hasattr(KISClient, "get_daily_prices")

    @patch("prime_jennie.infra.kis.client.get_config")
    def test_get_daily_prices_returns_list(self, mock_config):
        from prime_jennie.domain.stock import DailyPrice
        from prime_jennie.infra.kis.client import KISClient

        mock_config.return_value.kis.gateway_url = "http://fake:8080"

        client = KISClient.__new__(KISClient)
        mock_http = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {
                "stock_code": "005930",
                "price_date": "2026-02-19",
                "open_price": 70000,
                "high_price": 71000,
                "low_price": 69000,
                "close_price": 70500,
                "volume": 10000,
            }
        ]
        mock_http.post.return_value = mock_resp
        client._client = mock_http

        result = client.get_daily_prices("005930", days=30)
        assert len(result) == 1
        assert isinstance(result[0], DailyPrice)
        assert result[0].close_price == 70500


# ─── BuyExecutor ATR with daily prices ─────────────────────────


class TestExecutorATR:
    """BuyExecutor._calculate_atr — 일봉 기반 ATR."""

    @patch("prime_jennie.domain.config.get_config")
    def test_atr_uses_daily_prices(self, mock_config):
        from prime_jennie.domain.stock import DailyPrice
        from prime_jennie.services.buyer.executor import BuyExecutor

        mock_config.return_value.kis.gateway_url = "http://fake:8080"
        mock_config.return_value.risk.max_position_value_pct = 10

        mock_kis = MagicMock()
        prices = [
            DailyPrice(
                stock_code="005930",
                price_date=date(2026, 2, i + 1),
                open_price=70000 + i * 100,
                high_price=71000 + i * 100,
                low_price=69000 + i * 100,
                close_price=70500 + i * 100,
                volume=10000,
            )
            for i in range(20)
        ]
        mock_kis.get_daily_prices.return_value = prices

        executor = BuyExecutor.__new__(BuyExecutor)
        executor._kis = mock_kis
        executor._config = mock_config.return_value

        atr = executor._calculate_atr("005930", 72000)
        assert atr > 0
        mock_kis.get_daily_prices.assert_called_once_with("005930", days=30)

    @patch("prime_jennie.domain.config.get_config")
    def test_atr_fallback_on_error(self, mock_config):
        from prime_jennie.services.buyer.executor import BuyExecutor

        mock_config.return_value.kis.gateway_url = "http://fake:8080"

        mock_kis = MagicMock()
        mock_kis.get_daily_prices.side_effect = Exception("network error")

        executor = BuyExecutor.__new__(BuyExecutor)
        executor._kis = mock_kis
        executor._config = mock_config.return_value

        atr = executor._calculate_atr("005930", 70000)
        # 폴백: 2% of current_price, clamped
        assert atr == clamp_atr(70000 * 0.02, 70000)


# ─── PriceMonitor RSI ─────────────────────────────────────────


class TestMonitorRSI:
    """PriceMonitor._compute_rsi 메서드."""

    def test_compute_rsi_returns_value(self):
        from prime_jennie.domain.stock import DailyPrice
        from prime_jennie.services.monitor.app import PriceMonitor

        mock_kis = MagicMock()
        prices = [
            DailyPrice(
                stock_code="005930",
                price_date=date(2026, 2, i + 1),
                open_price=70000,
                high_price=71000,
                low_price=69000,
                close_price=70000 + i * 100,  # 순 상승
                volume=10000,
            )
            for i in range(20)
        ]
        mock_kis.get_daily_prices.return_value = prices

        monitor = PriceMonitor.__new__(PriceMonitor)
        monitor._kis = mock_kis

        rsi = monitor._compute_rsi("005930")
        assert rsi is not None
        assert 0.0 <= rsi <= 100.0

    def test_compute_rsi_returns_none_on_error(self):
        from prime_jennie.services.monitor.app import PriceMonitor

        mock_kis = MagicMock()
        mock_kis.get_daily_prices.side_effect = Exception("fail")

        monitor = PriceMonitor.__new__(PriceMonitor)
        monitor._kis = mock_kis

        rsi = monitor._compute_rsi("005930")
        assert rsi is None

    def test_compute_rsi_returns_none_insufficient_data(self):
        from prime_jennie.domain.stock import DailyPrice
        from prime_jennie.services.monitor.app import PriceMonitor

        mock_kis = MagicMock()
        mock_kis.get_daily_prices.return_value = [
            DailyPrice(
                stock_code="005930",
                price_date=date(2026, 2, i + 1),
                open_price=70000,
                high_price=71000,
                low_price=69000,
                close_price=70000,
                volume=10000,
            )
            for i in range(5)  # 15개 미만
        ]

        monitor = PriceMonitor.__new__(PriceMonitor)
        monitor._kis = mock_kis

        rsi = monitor._compute_rsi("005930")
        assert rsi is None
