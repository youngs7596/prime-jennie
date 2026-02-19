"""KIS Gateway 서비스 단위 테스트."""

import sys
from datetime import UTC, date, datetime
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from prime_jennie.domain.stock import DailyPrice, StockSnapshot


@pytest.fixture(autouse=True)
def _reset_caches():
    from prime_jennie.domain.config import get_config

    get_config.cache_clear()
    yield
    get_config.cache_clear()


def _get_gateway_module():
    """Gateway app 모듈을 안전하게 가져오기."""

    return sys.modules["prime_jennie.services.gateway.app"]


@pytest.fixture
def mock_kis_api():
    """KIS API 모의 객체."""
    with patch("prime_jennie.services.gateway.app._get_kis_api") as mock_factory:
        mock_api = MagicMock()
        mock_factory.return_value = mock_api
        yield mock_api


@pytest.fixture
def client(mock_kis_api):
    """FastAPI TestClient with mocked KIS API."""
    gw = _get_gateway_module()
    gw._kis_api = None
    return TestClient(gw.app)


@pytest.fixture
def mock_circuit_breaker():
    """Circuit breaker 모의 객체 — 테스트 후 원복."""
    gw = _get_gateway_module()
    original_cb = gw._circuit_breaker
    mock_cb = MagicMock()
    gw._circuit_breaker = mock_cb
    yield mock_cb
    gw._circuit_breaker = original_cb


class TestMarketSnapshot:
    """POST /api/market/snapshot 테스트."""

    def test_valid_snapshot(self, client, mock_kis_api):
        mock_kis_api.get_snapshot.return_value = StockSnapshot(
            stock_code="005930",
            price=72100,
            open_price=71500,
            high_price=72500,
            low_price=71000,
            volume=15000000,
            change_pct=1.5,
            timestamp=datetime.now(UTC),
        )

        resp = client.post("/api/market/snapshot", json={"stock_code": "005930"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["stock_code"] == "005930"
        assert data["price"] == 72100

    def test_invalid_stock_code_rejected(self, client, mock_kis_api):
        resp = client.post("/api/market/snapshot", json={"stock_code": "invalid"})
        assert resp.status_code == 422

    def test_circuit_breaker_open(self, client, mock_circuit_breaker):
        import pybreaker

        mock_circuit_breaker.call.side_effect = pybreaker.CircuitBreakerError()

        resp = client.post("/api/market/snapshot", json={"stock_code": "005930"})
        assert resp.status_code == 503


class TestDailyPrices:
    """POST /api/market/daily-prices 테스트."""

    def test_returns_price_list(self, client, mock_circuit_breaker):
        mock_circuit_breaker.call.return_value = [
            DailyPrice(
                stock_code="005930",
                price_date=date(2026, 2, 18),
                open_price=71000,
                high_price=72000,
                low_price=70500,
                close_price=71800,
                volume=10000000,
            ),
        ]

        resp = client.post("/api/market/daily-prices", json={"stock_code": "005930", "days": 5})
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["stock_code"] == "005930"


class TestTradingEndpoints:
    """Trading 엔드포인트 테스트."""

    def test_buy_order_success(self, client, mock_circuit_breaker):
        mock_circuit_breaker.call.return_value = {"order_no": "0001234567"}

        resp = client.post(
            "/api/trading/buy",
            json={"stock_code": "005930", "quantity": 10, "order_type": "market"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["stock_code"] == "005930"

    def test_cancel_order(self, client, mock_circuit_breaker):
        mock_circuit_breaker.call.return_value = True

        resp = client.post("/api/trading/cancel", json={"order_no": "0001234567"})
        assert resp.status_code == 200
        assert resp.json()["success"] is True


class TestHealthEndpoint:
    """GET /health 테스트."""

    def test_health_returns_status(self, client):
        with patch("prime_jennie.services.base._check_dependency") as mock_dep:
            from prime_jennie.domain.health import DependencyHealth

            mock_dep.return_value = DependencyHealth(status="healthy", latency_ms=1.0)

            resp = client.get("/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["service"] == "kis-gateway"
            assert data["status"] in ("healthy", "degraded", "unhealthy")


class TestMarketOpen:
    """GET /api/market/is-market-open 테스트."""

    def test_returns_session_info(self, client):
        resp = client.get("/api/market/is-market-open")
        assert resp.status_code == 200
        data = resp.json()
        assert "is_open" in data
        assert "session" in data
