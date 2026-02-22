"""E2E 라운드트립 테스트 — 매수 → 매도 전체 사이클.

BuyExecutor + SellExecutor를 순차적으로 실행하여 전체 파이프라인 검증.
"""

import os
from unittest.mock import patch

import httpx
import pytest
from sqlmodel import Session, select

from prime_jennie.domain.config import get_config
from prime_jennie.domain.enums import SellReason
from prime_jennie.infra.database.models import PositionDB, TradeLogDB
from prime_jennie.infra.kis.client import KISClient
from prime_jennie.services.buyer.app import _persist_buy
from prime_jennie.services.buyer.executor import BuyExecutor
from prime_jennie.services.buyer.portfolio_guard import PortfolioGuard
from prime_jennie.services.seller.app import _persist_sell

from .mock_gateway import create_mock_transport

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# 1. 매수 → 매도 라운드트립
# ---------------------------------------------------------------------------


def test_buy_then_sell_roundtrip(
    buy_executor,
    sell_executor,
    mock_gateway_state,
    test_engine,
    make_buy_signal,
    make_sell_order,
):
    """매수 → 포지션 설정 → 매도 → trade_logs에 BUY+SELL 2건."""
    mock_gateway_state.prices["005930"] = 65000
    mock_gateway_state.cash_balance = 100_000_000

    # Step 1: 매수
    signal = make_buy_signal(stock_code="005930", hybrid_score=75.0)
    buy_result = buy_executor.process_signal(signal)
    assert buy_result.status == "success"

    # DB 저장
    _persist_buy(signal, buy_result)

    # Step 2: Gateway 포지션 업데이트 (매수 반영)
    mock_gateway_state.positions = [
        {
            "stock_code": "005930",
            "stock_name": "삼성전자",
            "quantity": buy_result.quantity,
            "average_buy_price": buy_result.price,
            "total_buy_amount": buy_result.quantity * buy_result.price,
        }
    ]
    mock_gateway_state.prices["005930"] = 72000  # 가격 상승

    # Step 3: 매도
    order = make_sell_order(
        stock_code="005930",
        quantity=buy_result.quantity,
        current_price=72000,
        buy_price=buy_result.price,
        sell_reason=SellReason.PROFIT_TARGET,
    )
    sell_result = sell_executor.process_signal(order)
    assert sell_result.status == "success"

    # DB 저장
    _persist_sell(order, sell_result)

    # 검증: trade_logs에 BUY + SELL 2건
    with Session(test_engine) as session:
        trades = list(session.exec(select(TradeLogDB).order_by(TradeLogDB.id)).all())
        assert len(trades) == 2
        assert trades[0].trade_type == "BUY"
        assert trades[1].trade_type == "SELL"
        assert trades[0].stock_code == trades[1].stock_code == "005930"

        # 전량 매도 → position 삭제
        pos = session.get(PositionDB, "005930")
        assert pos is None


# ---------------------------------------------------------------------------
# 2. 수익률 계산 검증
# ---------------------------------------------------------------------------


def test_profit_calculation(
    sell_executor,
    mock_gateway_state,
    make_sell_order,
):
    """buy_price=60000, sell_price=66000 → profit_pct ≈ 10%."""
    mock_gateway_state.positions = [
        {
            "stock_code": "005930",
            "stock_name": "삼성전자",
            "quantity": 100,
            "average_buy_price": 60000,
            "total_buy_amount": 6_000_000,
        }
    ]
    mock_gateway_state.prices["005930"] = 66000

    order = make_sell_order(
        stock_code="005930",
        quantity=100,
        current_price=66000,
        buy_price=60000,
    )
    result = sell_executor.process_signal(order)

    assert result.status == "success"
    assert result.profit_pct == pytest.approx(10.0, abs=0.1)


# ---------------------------------------------------------------------------
# 3. DRYRUN 모드 — 실주문 없이 가짜 주문번호 반환
# ---------------------------------------------------------------------------


def test_dryrun_mode(mock_gateway_state, test_redis, make_buy_signal):
    """dry_run=True → DRYRUN-0000 반환, gateway 호출 안 함."""
    mock_gateway_state.prices["005930"] = 65000
    mock_gateway_state.cash_balance = 100_000_000

    # dry_run 활성화
    get_config.cache_clear()
    with patch.dict(os.environ, {"APP_DRY_RUN": "true"}, clear=False):
        transport = create_mock_transport(mock_gateway_state)
        client = KISClient.__new__(KISClient)
        client._base_url = "http://mock"
        client._timeout = 30.0
        client._client = httpx.Client(transport=transport, base_url="http://mock")

        guard = PortfolioGuard(test_redis)
        executor = BuyExecutor(client, test_redis, guard)

        signal = make_buy_signal(stock_code="005930", hybrid_score=75.0)
        result = executor.process_signal(signal)

    get_config.cache_clear()

    assert result.status == "success"
    assert result.order_no == "DRYRUN-0000"
    # gateway의 order_no 카운터는 변하지 않아야 함 (실주문 안 했으므로)
    assert mock_gateway_state.next_order_no == 1
