"""Mock KIS Gateway — httpx.MockTransport 기반.

실제 KIS Gateway의 12개 엔드포인트를 mock 구현.
httpx.MockTransport로 네트워크 없이 sync httpx.Client에서 직접 호출.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import httpx

from .state import GatewayState

DEFAULT_PRICE = 65000


def _json_response(data, status_code: int = 200) -> httpx.Response:
    """JSON 응답 헬퍼."""
    return httpx.Response(
        status_code=status_code,
        json=data,
    )


def create_mock_transport(state: GatewayState) -> httpx.MockTransport:
    """GatewayState에 연결된 MockTransport 생성.

    httpx.Client(transport=...) 에 사용.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method

        # --- Health ---
        if path == "/health" and method == "GET":
            return _json_response({"status": "ok"})

        # --- Market status (GET) ---
        if path == "/api/market/is-trading-day" and method == "GET":
            return _json_response({"is_trading_day": state.is_trading_day})

        if path == "/api/market/is-market-open" and method == "GET":
            return _json_response({"is_market_open": state.is_market_open})

        # --- Parse JSON body for POST endpoints ---
        payload = {}
        if method == "POST" and request.content:
            payload = json.loads(request.content)

        # --- Trading ---
        if path == "/api/trading/buy" and method == "POST":
            return _handle_order(state, payload)

        if path == "/api/trading/sell" and method == "POST":
            return _handle_order(state, payload)

        if path == "/api/trading/cancel" and method == "POST":
            return _handle_cancel(state, payload)

        if path == "/api/trading/order-status" and method == "POST":
            return _handle_order_status(state, payload)

        # --- Account ---
        if path == "/api/account/balance" and method == "POST":
            return _json_response(
                {
                    "cash_balance": state.cash_balance,
                    "positions": state.positions,
                }
            )

        if path == "/api/account/cash" and method == "POST":
            return _json_response({"cash_balance": state.cash_balance})

        # --- Market data ---
        if path == "/api/market/snapshot" and method == "POST":
            return _handle_snapshot(state, payload)

        if path == "/api/market/daily-prices" and method == "POST":
            return _handle_daily_prices(state, payload)

        if path == "/api/market/minute-prices" and method == "POST":
            return _json_response([])

        # 404
        return httpx.Response(status_code=404, json={"error": f"Not found: {method} {path}"})

    return httpx.MockTransport(handler)


def _handle_order(state: GatewayState, payload: dict) -> httpx.Response:
    """주문(매수/매도) 처리."""
    stock_code = payload.get("stock_code", "")
    quantity = payload.get("quantity", 0)
    price = state.prices.get(stock_code, DEFAULT_PRICE)

    if state.order_should_fail:
        return _json_response(
            {
                "success": False,
                "order_no": None,
                "stock_code": stock_code,
                "quantity": quantity,
                "price": price,
                "message": state.order_fail_message,
            }
        )

    order_no = state.issue_order_no()
    state.issued_orders[order_no] = {
        "stock_code": stock_code,
        "quantity": quantity,
        "price": price,
    }

    return _json_response(
        {
            "success": True,
            "order_no": order_no,
            "stock_code": stock_code,
            "quantity": quantity,
            "price": price,
            "message": "filled",
        }
    )


def _handle_cancel(state: GatewayState, payload: dict) -> httpx.Response:
    """주문 취소."""
    order_no = payload.get("order_no", "")
    if state.cancel_should_fail:
        return _json_response({"success": False, "order_no": order_no})
    return _json_response({"success": True, "order_no": order_no})


def _handle_order_status(state: GatewayState, payload: dict) -> httpx.Response:
    """주문 체결 상태 조회."""
    order_no = payload.get("order_no", "")

    # 개별 주문 오버라이드
    if order_no in state.order_fill_overrides:
        return _json_response(state.order_fill_overrides[order_no])

    # 기본 동작: issued_orders 참조
    if state.default_filled and order_no in state.issued_orders:
        order_info = state.issued_orders[order_no]
        fill_price = state.fill_price_override or order_info["price"]
        return _json_response(
            {
                "filled": True,
                "filled_qty": order_info["quantity"],
                "avg_price": float(fill_price),
            }
        )

    # 미체결
    return _json_response({"filled": False, "filled_qty": 0, "avg_price": 0.0})


def _handle_snapshot(state: GatewayState, payload: dict) -> httpx.Response:
    """현재가 스냅샷."""
    code = payload.get("stock_code", "")
    price = state.prices.get(code, DEFAULT_PRICE)
    now = datetime.now(UTC)
    return _json_response(
        {
            "stock_code": code,
            "price": price,
            "open_price": price,
            "high_price": int(price * 1.02),
            "low_price": int(price * 0.98),
            "volume": 1_000_000,
            "change_pct": 0.5,
            "timestamp": now.isoformat(),
        }
    )


def _handle_daily_prices(state: GatewayState, payload: dict) -> httpx.Response:
    """일봉 데이터."""
    code = payload.get("stock_code", "")
    days = payload.get("days", 30)

    if code in state.daily_prices:
        return _json_response(state.daily_prices[code][:days])

    # 자동 생성
    base_price = state.prices.get(code, DEFAULT_PRICE)
    result = []
    today = date.today()
    for i in range(days):
        d = today - timedelta(days=days - i)
        p = base_price + (i - days // 2) * 100
        result.append(
            {
                "stock_code": code,
                "price_date": d.isoformat(),
                "open_price": p,
                "high_price": int(p * 1.02),
                "low_price": int(p * 0.98),
                "close_price": p,
                "volume": 500_000,
                "change_pct": 0.3,
            }
        )
    return _json_response(result)
