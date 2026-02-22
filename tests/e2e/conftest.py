"""E2E 테스트 공용 Fixtures.

Mock KIS Gateway + FakeRedis + SQLite in-memory DB로
BuyExecutor/SellExecutor를 외부 의존 없이 구동.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from unittest.mock import patch

import fakeredis
import httpx
import pytest
from sqlmodel import Session, SQLModel, create_engine

from prime_jennie.domain.config import get_config
from prime_jennie.domain.enums import (
    MarketRegime,
    SellReason,
    SignalType,
    TradeTier,
)
from prime_jennie.domain.trading import BuySignal, SellOrder
from prime_jennie.infra.database.engine import get_engine
from prime_jennie.infra.database.models import StockMasterDB
from prime_jennie.infra.kis.client import KISClient
from prime_jennie.services.buyer.executor import BuyExecutor
from prime_jennie.services.buyer.portfolio_guard import PortfolioGuard
from prime_jennie.services.seller.executor import SellExecutor

from .mock_gateway import GatewayState, create_mock_transport

# ---------------------------------------------------------------------------
# Config patching
# ---------------------------------------------------------------------------

_TEST_ENV = {
    "APP_DRY_RUN": "false",
    "APP_ENV": "test",
    "APP_TRADING_MODE": "REAL",
    "DB_HOST": "",
    "DB_PORT": "0",
    "DB_USER": "",
    "DB_PASSWORD": "",
    "DB_NAME": "",
    "REDIS_HOST": "localhost",
    "REDIS_PORT": "6379",
    "KIS_GATEWAY_URL": "http://mock",
    "KIS_APP_KEY": "test",
    "KIS_APP_SECRET": "test",
    "KIS_ACCOUNT_NO": "00000000-00",
    "RISK_PORTFOLIO_GUARD_ENABLED": "true",
    "RISK_DYNAMIC_SECTOR_BUDGET_ENABLED": "false",
    "SCORING_HARD_FLOOR_SCORE": "40",
}


@pytest.fixture(autouse=True)
def _patch_config():
    """모든 E2E 테스트에서 config 캐시를 클리어하고 테스트 환경 변수 주입."""
    get_config.cache_clear()
    with patch.dict(os.environ, _TEST_ENV, clear=False):
        yield
    get_config.cache_clear()


# ---------------------------------------------------------------------------
# Mock KIS Gateway
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_gateway_state() -> GatewayState:
    """Mutable gateway 상태 — 테스트에서 직접 변경."""
    return GatewayState()


@pytest.fixture
def mock_kis_client(mock_gateway_state: GatewayState) -> KISClient:
    """MockTransport 기반 KISClient (네트워크 없음)."""
    transport = create_mock_transport(mock_gateway_state)

    client = KISClient.__new__(KISClient)
    client._base_url = "http://mock"
    client._timeout = 30.0
    client._client = httpx.Client(transport=transport, base_url="http://mock")
    return client


# ---------------------------------------------------------------------------
# FakeRedis
# ---------------------------------------------------------------------------


@pytest.fixture
def test_redis() -> fakeredis.FakeRedis:
    """격리된 FakeRedis 인스턴스."""
    r = fakeredis.FakeRedis(version=(7,), decode_responses=True)
    yield r
    r.flushall()
    r.close()


# ---------------------------------------------------------------------------
# SQLite in-memory DB
# ---------------------------------------------------------------------------


@pytest.fixture
def test_engine():
    """SQLite in-memory engine + 시드 데이터.

    get_engine 및 서비스 모듈의 get_engine 참조를 모두 패치.
    """
    engine = create_engine("sqlite://", echo=False)
    SQLModel.metadata.create_all(engine)

    # FK용 시드 데이터 — 테스트에서 사용하는 종목 코드
    with Session(engine) as session:
        session.add(
            StockMasterDB(
                stock_code="005930",
                stock_name="삼성전자",
                market="KOSPI",
                sector_group="반도체/IT",
                is_active=True,
            )
        )
        session.add(
            StockMasterDB(
                stock_code="000660",
                stock_name="SK하이닉스",
                market="KOSPI",
                sector_group="반도체/IT",
                is_active=True,
            )
        )
        session.commit()

    get_engine.cache_clear()
    with (
        patch("prime_jennie.infra.database.engine.get_engine", return_value=engine),
        patch("prime_jennie.services.buyer.app.get_engine", return_value=engine),
        patch("prime_jennie.services.seller.app.get_engine", return_value=engine),
    ):
        yield engine
    get_engine.cache_clear()


# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------


@pytest.fixture
def buy_executor(mock_kis_client: KISClient, test_redis: fakeredis.FakeRedis) -> BuyExecutor:
    """테스트용 BuyExecutor."""
    guard = PortfolioGuard(test_redis)
    return BuyExecutor(mock_kis_client, test_redis, guard)


@pytest.fixture
def sell_executor(mock_kis_client: KISClient, test_redis: fakeredis.FakeRedis) -> SellExecutor:
    """테스트용 SellExecutor."""
    return SellExecutor(mock_kis_client, test_redis)


# ---------------------------------------------------------------------------
# Signal/Order Factories
# ---------------------------------------------------------------------------


@pytest.fixture
def make_buy_signal():
    """BuySignal 팩토리 — 기본값으로 유효한 시그널 생성."""

    def _factory(
        stock_code: str = "005930",
        stock_name: str = "삼성전자",
        signal_type: SignalType = SignalType.GOLDEN_CROSS,
        signal_price: int = 65000,
        llm_score: float = 75.0,
        hybrid_score: float = 72.0,
        trade_tier: TradeTier = TradeTier.TIER1,
        market_regime: MarketRegime = MarketRegime.BULL,
        **kwargs,
    ) -> BuySignal:
        return BuySignal(
            stock_code=stock_code,
            stock_name=stock_name,
            signal_type=signal_type,
            signal_price=signal_price,
            llm_score=llm_score,
            hybrid_score=hybrid_score,
            trade_tier=trade_tier,
            market_regime=market_regime,
            timestamp=datetime.now(UTC),
            **kwargs,
        )

    return _factory


@pytest.fixture
def make_sell_order():
    """SellOrder 팩토리 — 기본값으로 유효한 매도 주문 생성."""

    def _factory(
        stock_code: str = "005930",
        stock_name: str = "삼성전자",
        sell_reason: SellReason = SellReason.PROFIT_TARGET,
        current_price: int = 70000,
        quantity: int = 100,
        buy_price: int | None = 60000,
        profit_pct: float | None = None,
        holding_days: int | None = 5,
        **kwargs,
    ) -> SellOrder:
        return SellOrder(
            stock_code=stock_code,
            stock_name=stock_name,
            sell_reason=sell_reason,
            current_price=current_price,
            quantity=quantity,
            buy_price=buy_price,
            profit_pct=profit_pct,
            holding_days=holding_days,
            timestamp=datetime.now(UTC),
            **kwargs,
        )

    return _factory
