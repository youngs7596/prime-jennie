"""Mock KIS Gateway 상태 관리.

테스트 시나리오에 따라 mock 응답을 결정하는 Mutable 상태 객체.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GatewayState:
    """Mock Gateway의 런타임 상태.

    테스트에서 직접 변경하여 시나리오 설정.
    """

    # 계좌
    cash_balance: int = 100_000_000  # 1억
    positions: list[dict] = field(default_factory=list)

    # 주문
    next_order_no: int = 1
    order_should_fail: bool = False
    order_fail_message: str = "Order rejected"

    # 가격 {stock_code: price}
    prices: dict[str, int] = field(default_factory=dict)
    # 일봉 {stock_code: [dict]}
    daily_prices: dict[str, list[dict]] = field(default_factory=dict)

    # 시장 상태
    is_market_open: bool = True
    is_trading_day: bool = True

    def issue_order_no(self) -> str:
        """주문 번호 발급."""
        no = f"MOCK-{self.next_order_no:04d}"
        self.next_order_no += 1
        return no


def empty_portfolio(cash: int = 100_000_000) -> GatewayState:
    """빈 포트폴리오 상태."""
    return GatewayState(cash_balance=cash)


def with_holdings(positions: list[dict], cash: int = 50_000_000) -> GatewayState:
    """보유 종목이 설정된 상태."""
    return GatewayState(cash_balance=cash, positions=positions)


def with_prices(prices: dict[str, int], **kwargs) -> GatewayState:
    """종목별 현재가가 설정된 상태."""
    return GatewayState(prices=prices, **kwargs)
