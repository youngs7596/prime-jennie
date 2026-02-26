"""KIS Gateway HTTP Client — 도메인 모델 기반 주문 인터페이스.

KIS Gateway 서비스(kis-gateway:8080)와 통신.
도메인 모델(OrderRequest, OrderResult)을 사용하여 타입 안전성 보장.
"""

import logging
import time

import httpx

from prime_jennie.domain import (
    OrderRequest,
    OrderResult,
    OrderType,
    Position,
    StockSnapshot,
)
from prime_jennie.domain.config import get_config
from prime_jennie.domain.stock import DailyPrice, MinutePrice

logger = logging.getLogger(__name__)


class KISClient:
    """KIS Gateway HTTP 클라이언트.

    Usage:
        client = KISClient()
        result = client.buy(OrderRequest(stock_code="005930", quantity=10))
        balance = client.get_balance()
    """

    def __init__(self, base_url: str | None = None, timeout: float = 30.0):
        config = get_config()
        self._base_url = (base_url or config.kis.gateway_url).rstrip("/")
        self._timeout = timeout
        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=timeout,
            headers={"Content-Type": "application/json"},
        )

    def buy(self, order: OrderRequest) -> OrderResult:
        """매수 주문."""
        payload = {
            "stock_code": order.stock_code,
            "quantity": order.quantity,
            "order_type": order.order_type,
        }
        if order.order_type == OrderType.LIMIT and order.price:
            payload["price"] = order.price

        resp = self._client.post("/api/trading/buy", json=payload)
        resp.raise_for_status()
        return OrderResult.model_validate(resp.json())

    def sell(self, order: OrderRequest) -> OrderResult:
        """매도 주문."""
        payload = {
            "stock_code": order.stock_code,
            "quantity": order.quantity,
            "order_type": order.order_type,
        }
        if order.order_type == OrderType.LIMIT and order.price:
            payload["price"] = order.price

        resp = self._client.post("/api/trading/sell", json=payload)
        resp.raise_for_status()
        return OrderResult.model_validate(resp.json())

    def cancel_order(self, order_no: str) -> bool:
        """주문 취소."""
        resp = self._client.post("/api/trading/cancel", json={"order_no": order_no})
        resp.raise_for_status()
        return resp.json().get("success", False)

    def check_order_status(self, order_no: str) -> dict | None:
        """주문 체결 상태 조회.

        Returns:
            {"filled": bool, "filled_qty": int, "avg_price": float} 또는 실패 시 None
        """
        try:
            resp = self._client.post("/api/trading/order-status", json={"order_no": order_no})
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error("check_order_status failed: %s", e)
            return None

    def confirm_order(self, order_no: str, *, max_retries: int = 3, interval: float = 2.0) -> dict | None:
        """주문 체결 확인 (폴링).

        max_retries회 반복하며 전량 체결 여부 확인.

        Returns:
            {"filled_qty": int, "avg_price": float} 체결 시, None 미체결/실패 시
        """
        for attempt in range(max_retries):
            status = self.check_order_status(order_no)
            if status and status.get("filled"):
                return {
                    "filled_qty": status["filled_qty"],
                    "avg_price": status["avg_price"],
                }
            if attempt < max_retries - 1:
                time.sleep(interval)

        logger.warning("Order %s not fully filled after %d retries", order_no, max_retries)
        return None

    def get_balance(self) -> dict:
        """잔고 조회 (현금 + 보유 종목)."""
        resp = self._client.post("/api/account/balance")
        resp.raise_for_status()
        return resp.json()

    def get_positions(self) -> list[Position]:
        """보유 포지션 목록 (balance API에서 추출)."""
        data = self.get_balance()
        positions = data.get("positions", [])
        return [Position.model_validate(p) for p in positions]

    def get_cash_balance(self) -> int:
        """현금 잔고만 조회."""
        resp = self._client.post("/api/account/cash")
        resp.raise_for_status()
        return resp.json().get("cash_balance", 0)

    def get_daily_prices(self, stock_code: str, days: int = 150) -> list[DailyPrice]:
        """일봉 데이터 조회."""
        resp = self._client.post(
            "/api/market/daily-prices",
            json={"stock_code": stock_code, "days": days},
        )
        resp.raise_for_status()
        return [DailyPrice.model_validate(p) for p in resp.json()]

    def get_minute_prices(self, stock_code: str) -> list[MinutePrice]:
        """분봉 데이터 조회."""
        resp = self._client.post(
            "/api/market/minute-prices",
            json={"stock_code": stock_code},
        )
        resp.raise_for_status()
        return [MinutePrice.model_validate(p) for p in resp.json()]

    def get_price(self, stock_code: str) -> StockSnapshot:
        """현재가 스냅샷 조회."""
        resp = self._client.post(
            "/api/market/snapshot",
            json={"stock_code": stock_code},
        )
        resp.raise_for_status()
        return StockSnapshot.model_validate(resp.json())

    def health(self) -> bool:
        """게이트웨이 헬스체크."""
        try:
            resp = self._client.get("/health")
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    def close(self) -> None:
        """클라이언트 종료."""
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
