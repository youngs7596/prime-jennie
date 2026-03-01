"""Sell Executor — 매도 시그널 처리 및 주문 실행.

Price Monitor로부터 SellOrder를 수신하여 검증 후 KIS Gateway 주문.
"""

import contextlib
import logging
import zoneinfo
from datetime import datetime
from datetime import time as dt_time

import redis

from prime_jennie.domain import (
    OrderRequest,
    OrderType,
    Position,
    SellOrder,
)
from prime_jennie.domain.config import get_config
from prime_jennie.domain.enums import SellReason
from prime_jennie.infra.kis.client import KISClient

logger = logging.getLogger(__name__)

# Redis Keys
LOCK_PREFIX = "lock:sell:"
EMERGENCY_STOP_KEY = "trading:stopped"
COOLDOWN_PREFIX = "stoploss_cooldown:"
DRYRUN_KEY = "trading_flags:dryrun"
COOLDOWN_REASONS = {SellReason.STOP_LOSS, SellReason.DEATH_CROSS, SellReason.BREAKEVEN_STOP}

_KST = zoneinfo.ZoneInfo("Asia/Seoul")
_MARKET_OPEN = dt_time(9, 0)
_MARKET_CLOSE = dt_time(15, 30)


def _is_market_hours() -> bool:
    """현재 시각이 KST 09:00~15:30 이내인지 확인."""
    now_kst = datetime.now(_KST).time()
    return _MARKET_OPEN <= now_kst <= _MARKET_CLOSE


class SellResult:
    """매도 실행 결과."""

    __slots__ = (
        "status",
        "stock_code",
        "stock_name",
        "order_no",
        "quantity",
        "price",
        "profit_pct",
        "reason",
    )

    def __init__(
        self,
        status: str,
        stock_code: str = "",
        stock_name: str = "",
        order_no: str = "",
        quantity: int = 0,
        price: int = 0,
        profit_pct: float = 0.0,
        reason: str = "",
    ):
        self.status = status
        self.stock_code = stock_code
        self.stock_name = stock_name
        self.order_no = order_no
        self.quantity = quantity
        self.price = price
        self.profit_pct = profit_pct
        self.reason = reason

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "stock_code": self.stock_code,
            "stock_name": self.stock_name,
            "order_no": self.order_no,
            "quantity": self.quantity,
            "price": self.price,
            "profit_pct": self.profit_pct,
            "reason": self.reason,
        }


class SellExecutor:
    """매도 주문 실행 엔진.

    Args:
        kis_client: KIS Gateway 클라이언트
        redis_client: Redis 클라이언트
    """

    def __init__(
        self,
        kis_client: KISClient,
        redis_client: redis.Redis,
    ):
        self._config = get_config()
        self._kis = kis_client
        self._redis = redis_client

    def process_signal(self, order: SellOrder) -> SellResult:
        """매도 시그널 처리 파이프라인.

        Steps:
        0. Market hours check (MANUAL bypasses)
        1. Emergency stop check (MANUAL bypasses)
        2. Position validation
        3. Distributed lock
        4. Quantity validation
        5. Order execution
        6. Stop-loss cooldown (if applicable)
        """
        code = order.stock_code
        name = order.stock_name
        is_manual = order.sell_reason in (SellReason.MANUAL, SellReason.FORCED_LIQUIDATION)

        # 0. Market hours check (MANUAL은 통과)
        if not is_manual and not _is_market_hours():
            return SellResult("skipped", code, name, reason="Outside market hours")

        # 1. Emergency stop (MANUAL은 통과)
        if not is_manual and self._is_emergency_stopped():
            return SellResult("skipped", code, name, reason="Emergency stop active")

        # 2. Position validation
        positions = self._get_positions()
        held = {p.stock_code: p for p in positions}
        if code not in held:
            return SellResult("skipped", code, name, reason="Not holding")

        position = held[code]

        # 3. Distributed lock
        if not self._acquire_lock(code):
            return SellResult("skipped", code, name, reason="Lock acquisition failed")

        try:
            return self._execute_sell(order, position)
        finally:
            self._release_lock(code)

    def _execute_sell(self, order: SellOrder, position: Position) -> SellResult:
        """실제 매도 실행 (lock 내부)."""
        code = order.stock_code
        name = order.stock_name

        # Quantity validation: 보유 수량 초과 방지
        sell_qty = min(order.quantity, position.quantity)
        if sell_qty <= 0:
            return SellResult("skipped", code, name, reason="Nothing to sell")

        # Current price
        try:
            snapshot = self._kis.get_price(code)
            current_price = snapshot.price
        except Exception:
            current_price = order.current_price
            logger.warning("[%s] Price fetch failed, using signal price", code)

        if current_price <= 0:
            return SellResult("error", code, name, reason="Invalid price")

        # Profit calculation
        buy_price = position.average_buy_price
        profit_pct = 0.0
        if buy_price > 0:
            profit_pct = round((current_price - buy_price) / buy_price * 100, 2)

        # === DRYRUN 모드: 실주문 대신 가짜 성공 반환 ===
        if self._config.dry_run or self._redis.get(DRYRUN_KEY):
            logger.info(
                "[DRYRUN][%s] SELL %d shares at %d (skipping KIS API)",
                code,
                sell_qty,
                current_price,
            )
            if order.sell_reason in COOLDOWN_REASONS:
                self._set_cooldown(code)
            self._set_sell_cooldown(code)
            if sell_qty >= position.quantity:
                self._cleanup_position_state(code)
            return SellResult(
                "success",
                code,
                name,
                order_no="DRYRUN-0000",
                quantity=sell_qty,
                price=current_price,
                profit_pct=profit_pct,
            )

        # Order execution (시장가)
        order_req = OrderRequest(
            stock_code=code,
            quantity=sell_qty,
            order_type=OrderType.MARKET,
        )
        try:
            result = self._kis.sell(order_req)
        except Exception as e:
            logger.error("[%s] Sell order failed: %s", code, e)
            return SellResult("error", code, name, reason=f"Order failed: {e}")

        if not result.success:
            return SellResult(
                "error",
                code,
                name,
                reason=f"Order rejected: {result.message}",
            )

        # 체결 확인 (3회, 2초 간격)
        order_no = result.order_no or ""
        sell_price = current_price
        if order_no and order_no != "DRYRUN-0000":
            fill = self._kis.confirm_order(order_no)
            if fill:
                if fill["avg_price"] > 0:
                    sell_price = int(fill["avg_price"])
                logger.info("[%s] Sell confirmed: qty=%d, avg_price=%d", code, fill["filled_qty"], sell_price)
            else:
                # 매도 미체결 → 취소 시도 + 경고
                logger.error("[%s] SELL order %s NOT FILLED — attempting cancel", code, order_no)
                self._kis.cancel_order(order_no)
                return SellResult(
                    "error",
                    code,
                    name,
                    reason=f"Sell not filled, cancelled: {order_no}",
                )

        # 체결가로 수익률 재계산
        if buy_price > 0 and sell_price != current_price:
            profit_pct = round((sell_price - buy_price) / buy_price * 100, 2)

        logger.info(
            "[%s] SELL %d shares at %d (%s, profit=%.1f%%)",
            code,
            sell_qty,
            sell_price,
            order.sell_reason,
            profit_pct,
        )

        # Stop-loss / death-cross / breakeven cooldown
        if order.sell_reason in COOLDOWN_REASONS:
            self._set_cooldown(code)

        # 24h sell cooldown (모든 매도 사유)
        self._set_sell_cooldown(code)

        # Redis state cleanup for full exit
        if sell_qty >= position.quantity:
            self._cleanup_position_state(code)

        return SellResult(
            "success",
            code,
            name,
            order_no=order_no,
            quantity=sell_qty,
            price=sell_price,
            profit_pct=profit_pct,
        )

    # --- Helpers ---

    def _get_positions(self) -> list[Position]:
        try:
            return self._kis.get_positions()
        except Exception:
            logger.error("Failed to get positions")
            return []

    def _is_emergency_stopped(self) -> bool:
        try:
            return bool(self._redis.get(EMERGENCY_STOP_KEY))
        except Exception:
            return False

    def _acquire_lock(self, stock_code: str, ttl: int = 30) -> bool:
        try:
            return bool(self._redis.set(f"{LOCK_PREFIX}{stock_code}", "1", nx=True, ex=ttl))
        except Exception:
            return False

    def _release_lock(self, stock_code: str) -> None:
        with contextlib.suppress(Exception):
            self._redis.delete(f"{LOCK_PREFIX}{stock_code}")

    def _set_cooldown(self, stock_code: str) -> None:
        """Stop-loss 후 재매수 쿨다운 설정."""
        days = self._config.risk.stoploss_cooldown_days
        try:
            self._redis.setex(
                f"{COOLDOWN_PREFIX}{stock_code}",
                days * 86400,
                "1",
            )
            logger.info("[%s] Cooldown set: %d days", stock_code, days)
        except Exception:
            pass

    def _set_sell_cooldown(self, stock_code: str) -> None:
        """모든 매도 후 24시간 재매수 쿨다운 설정."""
        with contextlib.suppress(Exception):
            self._redis.setex(f"sell_cooldown:{stock_code}", 86400, "1")

    def _cleanup_position_state(self, stock_code: str) -> None:
        """전량 매도 시 Redis 상태 정리."""
        try:
            pipe = self._redis.pipeline()
            pipe.delete(f"watermark:{stock_code}")
            pipe.delete(f"scale_out:{stock_code}")
            pipe.delete(f"rsi_sold:{stock_code}")
            pipe.delete(f"profit_floor:{stock_code}")
            pipe.execute()
        except Exception:
            pass
