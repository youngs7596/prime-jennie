"""Sell Executor — 매도 시그널 처리 및 주문 실행.

Price Monitor로부터 SellOrder를 수신하여 검증 후 KIS Gateway 주문.
"""

import contextlib
import logging

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
        1. Emergency stop check (MANUAL bypasses)
        2. Position validation
        3. Distributed lock
        4. Quantity validation
        5. Order execution
        6. Stop-loss cooldown (if applicable)
        """
        code = order.stock_code
        name = order.stock_name
        is_manual = order.sell_reason == SellReason.MANUAL

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
        if self._config.dry_run:
            logger.info(
                "[DRYRUN][%s] SELL %d shares at %d (skipping KIS API)",
                code, sell_qty, current_price,
            )
            if order.sell_reason == SellReason.STOP_LOSS:
                self._set_cooldown(code)
            if sell_qty >= position.quantity:
                self._cleanup_position_state(code)
            return SellResult(
                "success", code, name,
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

        logger.info(
            "[%s] SELL %d shares at %d (%s, profit=%.1f%%)",
            code,
            sell_qty,
            current_price,
            order.sell_reason,
            profit_pct,
        )

        # Stop-loss cooldown
        if order.sell_reason == SellReason.STOP_LOSS:
            self._set_cooldown(code)

        # Redis state cleanup for full exit
        if sell_qty >= position.quantity:
            self._cleanup_position_state(code)

        return SellResult(
            "success",
            code,
            name,
            order_no=result.order_no or "",
            quantity=sell_qty,
            price=current_price,
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

    def _cleanup_position_state(self, stock_code: str) -> None:
        """전량 매도 시 Redis 상태 정리."""
        try:
            pipe = self._redis.pipeline()
            pipe.delete(f"watermark:{stock_code}")
            pipe.delete(f"scale_out:{stock_code}")
            pipe.delete(f"rsi_sold:{stock_code}")
            pipe.execute()
        except Exception:
            pass
