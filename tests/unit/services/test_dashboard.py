"""Dashboard API 단위 테스트."""

from datetime import UTC, date, datetime
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from prime_jennie.domain.enums import MarketRegime, TradeTier
from prime_jennie.domain.watchlist import HotWatchlist, WatchlistEntry


@pytest.fixture(autouse=True)
def _clear_config_cache():
    from prime_jennie.domain.config import get_config

    get_config.cache_clear()
    yield
    get_config.cache_clear()


# ─── Mock DB/Redis ──────────────────────────────────────────────


def _mock_position_db(**overrides):
    p = MagicMock()
    defaults = {
        "stock_code": "005930",
        "stock_name": "삼성전자",
        "quantity": 100,
        "average_buy_price": 70000,
        "total_buy_amount": 7_000_000,
        "sector_group": "반도체/IT",
        "high_watermark": 75000,
        "stop_loss_price": 63000,
    }
    defaults.update(overrides)
    for k, v in defaults.items():
        setattr(p, k, v)
    return p


def _mock_snapshot_db(**overrides):
    s = MagicMock()
    defaults = {
        "snapshot_date": date(2026, 2, 19),
        "total_asset": 50_000_000,
        "cash_balance": 20_000_000,
        "stock_eval_amount": 30_000_000,
        "total_profit_loss": 2_000_000,
        "realized_profit_loss": 500_000,
    }
    defaults.update(overrides)
    for k, v in defaults.items():
        setattr(s, k, v)
    return s


def _mock_trade_db(**overrides):
    t = MagicMock()
    defaults = {
        "id": 1,
        "stock_code": "005930",
        "stock_name": "삼성전자",
        "trade_type": "SELL",
        "quantity": 50,
        "price": 77000,
        "total_amount": 3_850_000,
        "reason": "PROFIT_TARGET",
        "strategy_signal": "GOLDEN_CROSS",
        "market_regime": "BULL",
        "llm_score": 75.0,
        "hybrid_score": 72.0,
        "trade_tier": "TIER1",
        "profit_pct": 10.0,
        "profit_amount": 350_000,
        "holding_days": 7,
        "trade_timestamp": datetime(2026, 2, 19, 10, 0, tzinfo=UTC),
    }
    defaults.update(overrides)
    for k, v in defaults.items():
        setattr(t, k, v)
    return t


def _mock_insight_db(**overrides):
    i = MagicMock()
    defaults = {
        "insight_date": date(2026, 2, 19),
        "sentiment": "neutral_to_bullish",
        "sentiment_score": 62,
        "regime_hint": "Selective_Buying",
        "position_size_pct": 95,
        "stop_loss_adjust_pct": 105,
        "political_risk_level": "medium",
        "political_risk_summary": "미중 관세 갈등",
        "vix_value": 18.5,
        "vix_regime": "normal",
        "usd_krw": 1320.5,
        "kospi_index": 2650.0,
        "kosdaq_index": 840.0,
        "sectors_to_favor": "반도체/IT",
        "sectors_to_avoid": "바이오/헬스케어",
        "sector_signals_json": None,
        "council_cost_usd": 0.215,
    }
    defaults.update(overrides)
    for k, v in defaults.items():
        setattr(i, k, v)
    return i


# ─── Test Client Factory ──────────────────────────────────────


def _make_client():
    """모든 DB/Redis 의존성을 mock한 TestClient."""
    from prime_jennie.services.dashboard.app import app
    from prime_jennie.services.deps import get_db_session, get_redis_client

    mock_session = MagicMock()
    mock_redis = MagicMock()
    mock_redis.hgetall.return_value = {}

    app.dependency_overrides[get_db_session] = lambda: mock_session
    app.dependency_overrides[get_redis_client] = lambda: mock_redis

    client = TestClient(app)
    return client, mock_session, mock_redis


# ─── Portfolio Tests ─────────────────────────────────────────


class TestPortfolioAPI:
    @patch("prime_jennie.services.dashboard.routers.portfolio.AssetSnapshotRepository")
    @patch("prime_jennie.services.dashboard.routers.portfolio.PortfolioRepository")
    def test_get_summary(self, mock_repo, mock_snap_repo):
        client, session, redis = _make_client()
        mock_repo.get_positions.return_value = [_mock_position_db()]
        mock_snap_repo.get_latest.return_value = _mock_snapshot_db()

        resp = client.get("/api/portfolio/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["position_count"] == 1
        assert data["cash_balance"] == 20_000_000
        assert data["total_asset"] == 50_000_000
        assert len(data["positions"]) == 1

    @patch("prime_jennie.services.dashboard.routers.portfolio.PortfolioRepository")
    def test_get_positions(self, mock_repo):
        client, _, _ = _make_client()
        mock_repo.get_positions.return_value = [
            _mock_position_db(),
            _mock_position_db(stock_code="000660", stock_name="SK하이닉스"),
        ]

        resp = client.get("/api/portfolio/positions")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["stock_code"] == "005930"
        assert data[1]["stock_code"] == "000660"

    @patch("prime_jennie.services.dashboard.routers.portfolio.AssetSnapshotRepository")
    def test_get_history(self, mock_repo):
        client, _, _ = _make_client()
        mock_repo.get_snapshots.return_value = [
            _mock_snapshot_db(snapshot_date=date(2026, 2, 18)),
            _mock_snapshot_db(snapshot_date=date(2026, 2, 19)),
        ]

        resp = client.get("/api/portfolio/history?days=7")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    @patch("prime_jennie.services.dashboard.routers.portfolio.PortfolioRepository")
    def test_get_performance(self, mock_repo):
        client, _, _ = _make_client()
        mock_repo.get_recent_trades.return_value = [
            _mock_trade_db(profit_pct=10.0, profit_amount=350_000),
            _mock_trade_db(id=2, profit_pct=-3.0, profit_amount=-100_000),
            _mock_trade_db(id=3, profit_pct=5.0, profit_amount=200_000),
        ]

        resp = client.get("/api/portfolio/performance?days=30")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_trades"] == 3
        assert data["win_trades"] == 2
        assert data["loss_trades"] == 1
        assert data["win_rate"] == pytest.approx(2 / 3, abs=0.01)
        assert data["total_profit"] == 450_000

    @patch("prime_jennie.services.dashboard.routers.portfolio.PortfolioRepository")
    def test_get_performance_no_trades(self, mock_repo):
        client, _, _ = _make_client()
        mock_repo.get_recent_trades.return_value = []

        resp = client.get("/api/portfolio/performance")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_trades"] == 0
        assert data["win_rate"] == 0.0

    @patch("prime_jennie.services.dashboard.routers.portfolio.AssetSnapshotRepository")
    @patch("prime_jennie.services.dashboard.routers.portfolio.PortfolioRepository")
    def test_summary_no_snapshot(self, mock_repo, mock_snap_repo):
        """스냅샷 없으면 buy_amount 합으로 대체."""
        client, _, _ = _make_client()
        mock_repo.get_positions.return_value = [_mock_position_db()]
        mock_snap_repo.get_latest.return_value = None

        resp = client.get("/api/portfolio/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["cash_balance"] == 0
        assert data["total_asset"] == 7_000_000  # total_buy_amount


# ─── Macro Tests ────────────────────────────────────────────


class TestMacroAPI:
    @patch("prime_jennie.services.dashboard.routers.macro.MacroRepository")
    def test_get_insight(self, mock_repo):
        client, _, _ = _make_client()
        mock_repo.get_latest_insight.return_value = _mock_insight_db()

        resp = client.get("/api/macro/insight")
        assert resp.status_code == 200
        data = resp.json()
        assert data["sentiment"] == "neutral_to_bullish"
        assert data["sentiment_score"] == 62
        assert data["vix_value"] == 18.5

    @patch("prime_jennie.services.dashboard.routers.macro.MacroRepository")
    def test_get_insight_by_date(self, mock_repo):
        client, _, _ = _make_client()
        mock_repo.get_insight_by_date.return_value = _mock_insight_db()

        resp = client.get("/api/macro/insight?target_date=2026-02-19")
        assert resp.status_code == 200
        data = resp.json()
        assert data["insight_date"] == "2026-02-19"

    @patch("prime_jennie.services.dashboard.routers.macro.MacroRepository")
    def test_get_insight_no_data(self, mock_repo):
        client, _, _ = _make_client()
        mock_repo.get_latest_insight.return_value = None

        resp = client.get("/api/macro/insight")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "no_data"

    @patch("prime_jennie.services.dashboard.routers.macro.MacroRepository")
    def test_get_regime(self, mock_repo):
        client, _, _ = _make_client()
        mock_repo.get_latest_insight.return_value = _mock_insight_db()

        resp = client.get("/api/macro/regime")
        assert resp.status_code == 200
        data = resp.json()
        assert data["regime"] == "BULL"
        assert data["position_multiplier"] == 0.95
        assert data["is_high_volatility"] is False

    @patch("prime_jennie.services.dashboard.routers.macro.MacroRepository")
    def test_get_regime_no_data(self, mock_repo):
        client, _, _ = _make_client()
        mock_repo.get_latest_insight.return_value = None

        resp = client.get("/api/macro/regime")
        assert resp.status_code == 200
        data = resp.json()
        assert data["regime"] == "SIDEWAYS"  # default


# ─── Watchlist Tests ──────────────────────────────────────────


class TestWatchlistAPI:
    def test_get_current_from_redis(self):
        client, _, mock_redis = _make_client()

        watchlist = HotWatchlist(
            generated_at=datetime(2026, 2, 19, 10, 0, tzinfo=UTC),
            market_regime=MarketRegime.BULL,
            stocks=[
                WatchlistEntry(
                    stock_code="005930",
                    stock_name="삼성전자",
                    llm_score=75,
                    hybrid_score=72,
                    rank=1,
                    is_tradable=True,
                    trade_tier=TradeTier.TIER1,
                )
            ],
            version="v1708333200",
        )
        mock_redis.get.return_value = watchlist.model_dump_json()

        resp = client.get("/api/watchlist/current")
        assert resp.status_code == 200
        data = resp.json()
        assert "stocks" in data

    def test_get_current_no_data(self):
        client, _, mock_redis = _make_client()
        mock_redis.get.return_value = None

        resp = client.get("/api/watchlist/current")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "no_data"

    @patch("prime_jennie.services.dashboard.routers.watchlist.WatchlistRepository")
    def test_get_history(self, mock_repo):
        client, _, _ = _make_client()
        entry = MagicMock()
        entry.snapshot_date = date(2026, 2, 19)
        entry.stock_code = "005930"
        entry.stock_name = "삼성전자"
        entry.llm_score = 75.0
        entry.hybrid_score = 72.0
        entry.is_tradable = True
        entry.trade_tier = "TIER1"
        entry.risk_tag = "NEUTRAL"
        entry.rank = 1
        mock_repo.get_latest.return_value = [entry]

        resp = client.get("/api/watchlist/history")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["stock_code"] == "005930"


# ─── Trades Tests ──────────────────────────────────────────────


class TestTradesAPI:
    @patch("prime_jennie.services.dashboard.routers.trades.PortfolioRepository")
    def test_get_recent_trades(self, mock_repo):
        client, _, _ = _make_client()
        mock_repo.get_recent_trades.return_value = [
            _mock_trade_db(),
            _mock_trade_db(id=2, stock_code="000660", stock_name="SK하이닉스"),
        ]

        resp = client.get("/api/trades/recent?days=14")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["stock_code"] == "005930"

    @patch("prime_jennie.services.dashboard.routers.trades.PortfolioRepository")
    def test_get_recent_empty(self, mock_repo):
        client, _, _ = _make_client()
        mock_repo.get_recent_trades.return_value = []

        resp = client.get("/api/trades/recent")
        assert resp.status_code == 200
        assert resp.json() == []


# ─── LLM Stats Tests ──────────────────────────────────────────


class TestLLMStatsAPI:
    def test_get_stats_by_date(self):
        client, _, mock_redis = _make_client()
        mock_redis.hgetall.side_effect = lambda key: {
            "llm:stats:2026-02-19:scout": {
                "calls": "15",
                "tokens_in": "50000",
                "tokens_out": "20000",
            },
            "llm:stats:2026-02-19:macro_council": {
                "calls": "2",
                "tokens_in": "30000",
                "tokens_out": "5000",
            },
        }.get(key, {})

        resp = client.get("/api/llm/stats/2026-02-19")
        assert resp.status_code == 200
        data = resp.json()
        assert data["date"] == "2026-02-19"
        assert data["total"]["calls"] == 17
        assert "scout" in data["services"]

    def test_get_stats_no_data(self):
        client, _, mock_redis = _make_client()
        mock_redis.hgetall.return_value = {}

        resp = client.get("/api/llm/stats/2026-02-19")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"]["calls"] == 0


# ─── System Tests ──────────────────────────────────────────────


class TestSystemAPI:
    @patch("prime_jennie.services.dashboard.routers.system._check_service")
    @pytest.mark.asyncio
    async def test_get_all_health(self, mock_check):
        from prime_jennie.services.dashboard.routers.system import ServiceStatus

        mock_check.return_value = ServiceStatus(name="test", port=8080, status="healthy", version="1.0.0")
        client, _, _ = _make_client()
        resp = client.get("/api/system/health")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)


# ─── Health Check Test ──────────────────────────────────────────


class TestHealthEndpoint:
    def test_health_returns_200(self):
        client, _, _ = _make_client()

        # Mock the dependency checks
        with patch("prime_jennie.services.base._check_dependency") as mock_check:
            from prime_jennie.domain.health import DependencyHealth

            mock_check.return_value = DependencyHealth(status="healthy", latency_ms=1.0)

            resp = client.get("/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["service"] == "dashboard"
            assert data["status"] == "healthy"
