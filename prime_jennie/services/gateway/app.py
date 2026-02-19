"""KIS Gateway Service — KIS API 중앙 프록시.

모든 KIS API 호출을 중앙화하여 레이트 리밋, 서킷 브레이커 적용.
다른 서비스는 이 Gateway를 통해서만 KIS API에 접근.

Endpoints (04-service-contracts.md):
    POST /api/market/snapshot     → StockSnapshot
    POST /api/market/daily-prices → List[DailyPrice]
    GET  /api/market/is-trading-day
    GET  /api/market/is-market-open
    POST /api/trading/buy         → OrderResult
    POST /api/trading/sell        → OrderResult
    POST /api/trading/cancel
    POST /api/account/balance     → PortfolioState
    POST /api/account/cash
    GET  /health                  → HealthStatus
"""

import logging
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from typing import Optional

import pybreaker
from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlmodel import Session

from prime_jennie.domain.config import get_config
from prime_jennie.domain.portfolio import PortfolioState, Position
from prime_jennie.domain.stock import DailyPrice, StockSnapshot
from prime_jennie.domain.trading import OrderRequest, OrderResult, OrderType
from prime_jennie.infra.database.repositories import StockRepository
from prime_jennie.services.base import create_app
from prime_jennie.services.deps import get_db_session

from .kis_api import KISApi, KISApiError

logger = logging.getLogger(__name__)

# ─── Rate Limiter ────────────────────────────────────────────────

# 글로벌 KIS 계정 레이트 리밋 (IP가 아닌 계정 기반)
_limiter = Limiter(key_func=lambda *args, **kwargs: "global_kis_account")


# ─── Circuit Breaker ─────────────────────────────────────────────


def _on_state_change(cb: pybreaker.CircuitBreaker, old_state: str, new_state: str) -> None:
    logger.warning("Circuit breaker state: %s → %s", old_state, new_state)


_circuit_breaker = pybreaker.CircuitBreaker(
    fail_max=20,
    reset_timeout=60,
    listeners=[pybreaker.CircuitBreakerListener()],
)

# ─── Request Stats ───────────────────────────────────────────────

_request_history: deque = deque(maxlen=100)

# ─── KIS API Client ─────────────────────────────────────────────

_kis_api: Optional[KISApi] = None


def _get_kis_api() -> KISApi:
    global _kis_api
    if _kis_api is None:
        _kis_api = KISApi()
    return _kis_api


# ─── Request/Response Models ────────────────────────────────────


class SnapshotRequest(BaseModel):
    stock_code: str = Field(pattern=r"^\d{6}$")


class DailyPricesRequest(BaseModel):
    stock_code: str = Field(pattern=r"^\d{6}$")
    days: int = Field(default=150, ge=1, le=500)


class CancelRequest(BaseModel):
    order_no: str


class TradingDayQuery(BaseModel):
    date: Optional[str] = None  # YYYY-MM-DD


# ─── Lifespan ────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app) -> AsyncIterator[None]:
    config = get_config()
    logger.info(
        "KIS Gateway starting — mode=%s, paper=%s",
        config.trading_mode,
        config.kis.is_paper,
    )
    # 시작 시 토큰 사전 발급
    try:
        api = _get_kis_api()
        api.authenticate()
        logger.info("KIS token pre-authenticated")
    except Exception as e:
        logger.warning("KIS pre-auth failed (will retry on first request): %s", e)

    yield

    # 종료
    if _kis_api:
        _kis_api.close()


# ─── App ─────────────────────────────────────────────────────────

app = create_app("kis-gateway", version="1.0.0", lifespan=lifespan, dependencies=["redis", "db"])

# Rate limit 에러 핸들러
app.state.limiter = _limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return {"error": "Rate limit exceeded", "detail": str(exc)}, 429


# ─── Market Endpoints ────────────────────────────────────────────


@app.post("/api/market/snapshot", response_model=StockSnapshot)
@_limiter.limit("19/second")
async def market_snapshot(request: Request, body: SnapshotRequest) -> StockSnapshot:
    """현재가 스냅샷 조회."""
    _record_request("snapshot", body.stock_code)
    try:
        return _circuit_breaker.call(_get_kis_api().get_snapshot, body.stock_code)
    except pybreaker.CircuitBreakerError:
        raise HTTPException(503, "Circuit breaker open — KIS API temporarily unavailable")
    except KISApiError as e:
        raise HTTPException(502, f"KIS API error: {e}")


@app.post("/api/market/daily-prices", response_model=list[DailyPrice])
@_limiter.limit("19/second")
async def market_daily_prices(
    request: Request,
    body: DailyPricesRequest,
    session: Session = Depends(get_db_session),
) -> list[DailyPrice]:
    """일봉 데이터 조회. KIS API 실패 시 DB 폴백."""
    _record_request("daily_prices", body.stock_code)
    try:
        return _circuit_breaker.call(_get_kis_api().get_daily_prices, body.stock_code, body.days)
    except (pybreaker.CircuitBreakerError, KISApiError):
        logger.warning("KIS daily-prices failed, falling back to DB for %s", body.stock_code)
        db_rows = StockRepository.get_daily_prices(session, body.stock_code, body.days)
        return [
            DailyPrice(
                stock_code=row.stock_code,
                price_date=row.price_date,
                open_price=row.open_price,
                high_price=row.high_price,
                low_price=row.low_price,
                close_price=row.close_price,
                volume=row.volume,
                change_pct=row.change_pct,
            )
            for row in db_rows
        ]


@app.get("/api/market/is-trading-day")
@_limiter.limit("5/second")
async def is_trading_day(request: Request, date: Optional[str] = None) -> dict:
    """거래일 여부 확인."""
    target = None
    if date:
        try:
            target = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD")

    try:
        result = _get_kis_api().is_trading_day(target)
        return {"is_trading_day": result}
    except Exception as e:
        logger.warning("is_trading_day check failed: %s", e)
        # 폴백: 주말만 체크
        check_date = target or datetime.now().date()
        return {"is_trading_day": check_date.weekday() < 5}


@app.get("/api/market/is-market-open")
@_limiter.limit("5/second")
async def is_market_open(request: Request) -> dict:
    """장 운영 상태 확인."""
    now = datetime.now(timezone.utc).astimezone()
    hour = now.hour
    minute = now.minute
    time_val = hour * 100 + minute

    if time_val < 900:
        session_str = "pre_market"
        is_open = False
    elif time_val < 930:
        session_str = "pre_opening"
        is_open = True
    elif time_val < 1530:
        session_str = "regular"
        is_open = True
    elif time_val < 1600:
        session_str = "closing"
        is_open = True
    else:
        session_str = "after_hours"
        is_open = False

    return {"is_open": is_open, "session": session_str}


# ─── Trading Endpoints ───────────────────────────────────────────


@app.post("/api/trading/buy", response_model=OrderResult)
@_limiter.limit("5/second")
async def trading_buy(request: Request, order: OrderRequest) -> OrderResult:
    """매수 주문."""
    _record_request("buy", order.stock_code)
    price = order.price if order.order_type == OrderType.LIMIT else 0
    try:
        result = _circuit_breaker.call(
            _get_kis_api().place_order,
            order_type="buy",
            stock_code=order.stock_code,
            quantity=order.quantity,
            price=price or 0,
        )
        return OrderResult(
            success=True,
            order_no=result.get("order_no"),
            stock_code=order.stock_code,
            quantity=order.quantity,
            price=price or 0,
        )
    except pybreaker.CircuitBreakerError:
        raise HTTPException(503, "Circuit breaker open")
    except KISApiError as e:
        return OrderResult(
            success=False,
            stock_code=order.stock_code,
            quantity=order.quantity,
            price=price or 0,
            message=str(e),
        )


@app.post("/api/trading/sell", response_model=OrderResult)
@_limiter.limit("5/second")
async def trading_sell(request: Request, order: OrderRequest) -> OrderResult:
    """매도 주문."""
    _record_request("sell", order.stock_code)
    price = order.price if order.order_type == OrderType.LIMIT else 0
    try:
        result = _circuit_breaker.call(
            _get_kis_api().place_order,
            order_type="sell",
            stock_code=order.stock_code,
            quantity=order.quantity,
            price=price or 0,
        )
        return OrderResult(
            success=True,
            order_no=result.get("order_no"),
            stock_code=order.stock_code,
            quantity=order.quantity,
            price=price or 0,
        )
    except pybreaker.CircuitBreakerError:
        raise HTTPException(503, "Circuit breaker open")
    except KISApiError as e:
        return OrderResult(
            success=False,
            stock_code=order.stock_code,
            quantity=order.quantity,
            price=price or 0,
            message=str(e),
        )


@app.post("/api/trading/cancel")
@_limiter.limit("5/second")
async def trading_cancel(request: Request, body: CancelRequest) -> dict:
    """주문 취소."""
    _record_request("cancel", body.order_no)
    try:
        success = _circuit_breaker.call(_get_kis_api().cancel_order, body.order_no)
        return {"success": success}
    except pybreaker.CircuitBreakerError:
        raise HTTPException(503, "Circuit breaker open")
    except KISApiError as e:
        return {"success": False, "error": str(e)}


# ─── Account Endpoints ───────────────────────────────────────────


@app.post("/api/account/balance", response_model=PortfolioState)
@_limiter.limit("5/second")
async def account_balance(request: Request) -> PortfolioState:
    """잔고 조회."""
    _record_request("balance", "account")
    try:
        data = _circuit_breaker.call(_get_kis_api().get_balance)
        positions = [
            Position(
                stock_code=p["stock_code"],
                stock_name=p["stock_name"],
                quantity=p["quantity"],
                average_buy_price=p["average_buy_price"],
                total_buy_amount=p["total_buy_amount"],
                current_price=p.get("current_price"),
                current_value=p.get("current_value"),
                profit_pct=p.get("profit_pct"),
            )
            for p in data.get("positions", [])
        ]
        return PortfolioState(
            positions=positions,
            cash_balance=data.get("cash_balance", 0),
            total_asset=data.get("total_asset", 0),
            stock_eval_amount=data.get("stock_eval_amount", 0),
            position_count=len(positions),
            timestamp=datetime.now(timezone.utc),
        )
    except pybreaker.CircuitBreakerError:
        raise HTTPException(503, "Circuit breaker open")
    except KISApiError as e:
        raise HTTPException(502, f"KIS API error: {e}")


@app.post("/api/account/cash")
@_limiter.limit("5/second")
async def account_cash(request: Request) -> dict:
    """현금 잔고만 조회."""
    _record_request("cash", "account")
    try:
        data = _circuit_breaker.call(_get_kis_api().get_balance)
        return {"cash_balance": data.get("cash_balance", 0)}
    except pybreaker.CircuitBreakerError:
        raise HTTPException(503, "Circuit breaker open")
    except KISApiError as e:
        raise HTTPException(502, f"KIS API error: {e}")


# ─── Helpers ─────────────────────────────────────────────────────


def _record_request(endpoint: str, detail: str) -> None:
    """요청 기록 (모니터링용)."""
    _request_history.append({
        "endpoint": endpoint,
        "detail": detail,
        "timestamp": time.time(),
    })
