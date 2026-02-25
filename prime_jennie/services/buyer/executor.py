"""Buy Executor — 매수 시그널 처리 및 주문 실행.

Scanner로부터 BuySignal을 수신하여 다단계 검증 후 KIS Gateway 주문.
"""

import contextlib
import logging
import time
from datetime import UTC, datetime

import redis

from prime_jennie.domain import (
    BuySignal,
    OrderRequest,
    OrderResult,
    OrderType,
    Position,
)
from prime_jennie.domain.config import get_config
from prime_jennie.domain.enums import MOMENTUM_STRATEGIES, TradeTier
from prime_jennie.domain.trading import PositionSizingRequest
from prime_jennie.infra.kis.client import KISClient

from .portfolio_guard import PortfolioGuard
from .position_sizing import (
    calculate_atr,
    calculate_position_size,
    clamp_atr,
    get_stale_multiplier,
)

logger = logging.getLogger(__name__)

# Redis 키
LOCK_PREFIX = "lock:buy:"
EMERGENCY_STOP_KEY = "trading:stopped"
TRADING_PAUSED_KEY = "trading:paused"
DRYRUN_KEY = "trading_flags:dryrun"


class ExecutionResult:
    """매수 실행 결과."""

    __slots__ = ("status", "stock_code", "stock_name", "order_no", "quantity", "price", "reason")

    def __init__(
        self,
        status: str,
        stock_code: str = "",
        stock_name: str = "",
        order_no: str = "",
        quantity: int = 0,
        price: int = 0,
        reason: str = "",
    ):
        self.status = status
        self.stock_code = stock_code
        self.stock_name = stock_name
        self.order_no = order_no
        self.quantity = quantity
        self.price = price
        self.reason = reason

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "stock_code": self.stock_code,
            "stock_name": self.stock_name,
            "order_no": self.order_no,
            "quantity": self.quantity,
            "price": self.price,
            "reason": self.reason,
        }


class BuyExecutor:
    """매수 주문 실행 엔진.

    Args:
        kis_client: KIS Gateway 클라이언트
        redis_client: Redis 클라이언트
        portfolio_guard: 포트폴리오 가드 (주입 가능)
    """

    def __init__(
        self,
        kis_client: KISClient,
        redis_client: redis.Redis,
        portfolio_guard: PortfolioGuard | None = None,
    ):
        self._config = get_config()
        self._kis = kis_client
        self._redis = redis_client
        self._guard = portfolio_guard or PortfolioGuard(redis_client)

    def process_signal(self, signal: BuySignal) -> ExecutionResult:
        """매수 시그널 처리 파이프라인.

        Steps:
        1. Emergency stop check
        2. BLOCKED tier check
        3. Hard floor check
        4. Already holding check
        5. Daily buy count check
        6. Distributed lock
        7. Position sizing
        8. Portfolio guard
        9. Order execution
        """
        code = signal.stock_code
        name = signal.stock_name

        # 1. Emergency stop
        if self._is_emergency_stopped():
            return ExecutionResult("skipped", code, name, reason="Emergency stop active")

        # 2. BLOCKED tier
        if signal.trade_tier == TradeTier.BLOCKED:
            return ExecutionResult("skipped", code, name, reason="BLOCKED tier (veto)")

        # 3. Hard floor
        if signal.hybrid_score < self._config.scoring.hard_floor_score:
            return ExecutionResult(
                "skipped",
                code,
                name,
                reason=f"Hard floor: score {signal.hybrid_score} < {self._config.scoring.hard_floor_score}",
            )

        # 4. Already holding
        positions = self._get_positions()
        if positions is None:
            return ExecutionResult("skipped", code, name, reason="Position fetch failed")
        held_codes = {p.stock_code for p in positions}
        if code in held_codes:
            return ExecutionResult("skipped", code, name, reason="Already holding")

        # 4-1. Cooldown check (매도 후 재매수 방지)
        cooldown_key = f"stoploss_cooldown:{code}"
        if self._redis.get(cooldown_key):
            return ExecutionResult("skipped", code, name, reason="Cooldown active")

        # 4-2. Sell cooldown (모든 매도 후 24h)
        if self._redis.get(f"sell_cooldown:{code}"):
            return ExecutionResult("skipped", code, name, reason="Sell cooldown (24h)")

        # 5. Daily buy count
        if not self._check_daily_limit():
            return ExecutionResult("skipped", code, name, reason="Daily buy limit reached")

        # 6. Portfolio size
        if len(positions) >= self._config.risk.max_portfolio_size:
            return ExecutionResult("skipped", code, name, reason="Portfolio full")

        # 7. Distributed lock
        if not self._acquire_lock(code):
            return ExecutionResult("skipped", code, name, reason="Lock acquisition failed")

        try:
            return self._execute_buy(signal, positions)
        finally:
            self._release_lock(code)

    def _execute_buy(self, signal: BuySignal, positions: list[Position]) -> ExecutionResult:
        """실제 매수 실행 (lock 내부)."""
        code = signal.stock_code
        name = signal.stock_name

        # Current price
        try:
            snapshot = self._kis.get_price(code)
            current_price = snapshot.price
        except Exception:
            current_price = signal.signal_price
            logger.warning("[%s] Price fetch failed, using signal price", code)

        if current_price <= 0:
            return ExecutionResult("error", code, name, reason="Invalid price")

        # ATR calculation
        atr = self._calculate_atr(code, current_price)

        # Correlation check
        if self._config.risk.correlation_check_enabled and positions:
            passed, max_corr, msg = self._check_correlation(code, positions)
            if not passed:
                return ExecutionResult("skipped", code, name, reason=msg)

        # Stale score
        stale_days = 0  # Scanner generates real-time signals
        get_stale_multiplier(stale_days)

        # Held sectors
        held_sectors = [p.sector_group for p in positions if p.sector_group is not None]

        # Position sizing
        balance = self._get_cash_balance()
        portfolio_value = sum((p.current_value or p.total_buy_amount) for p in positions)

        sizing_request = PositionSizingRequest(
            stock_code=code,
            stock_price=current_price,
            atr=atr,
            available_cash=balance,
            portfolio_value=portfolio_value,
            llm_score=signal.llm_score,
            trade_tier=signal.trade_tier,
            risk_tag=signal.risk_tag,
            sector_group=signal.sector_group,
            held_sector_groups=held_sectors,
            portfolio_risk_pct=0.0,
            position_multiplier=signal.position_multiplier,
            stale_days=stale_days,
        )
        sizing = calculate_position_size(sizing_request)

        if sizing.quantity <= 0:
            return ExecutionResult(
                "skipped",
                code,
                name,
                reason=f"Position size 0: {sizing.reasoning}",
            )

        # Portfolio Guard
        total_assets = balance + portfolio_value
        buy_amount = sizing.quantity * current_price

        from prime_jennie.domain.enums import SectorGroup

        guard_result = self._guard.check_all(
            sector_group=sizing_request.sector_group or SectorGroup.ETC,
            buy_amount=buy_amount,
            available_cash=balance,
            total_assets=total_assets,
            positions=positions,
            regime=signal.market_regime,
        )
        if not guard_result.passed:
            return ExecutionResult(
                "skipped",
                code,
                name,
                reason=f"Guard: {guard_result.reason}",
            )

        # Order execution
        order_result = self._place_order(signal, sizing.quantity, current_price)
        if not order_result.success:
            return ExecutionResult(
                "error",
                code,
                name,
                reason=f"Order failed: {order_result.message}",
            )

        self._increment_buy_count()
        self._cleanup_position_state(code)

        logger.info(
            "[%s] BUY %d shares at %d (signal=%s, tier=%s, hybrid=%.1f)",
            code,
            sizing.quantity,
            current_price,
            signal.signal_type,
            signal.trade_tier,
            signal.hybrid_score,
        )

        # 주문 성공 후 실제 체결가 조회 (시장가 체결가와 스냅샷 가격 차이 보정)
        actual_price = current_price
        try:
            fill_positions = self._kis.get_positions()
            for p in fill_positions:
                if p.stock_code == code:
                    actual_price = p.average_buy_price
                    break
        except Exception:
            logger.debug("[%s] Failed to fetch actual fill price, using snapshot", code)

        return ExecutionResult(
            "success",
            code,
            name,
            order_no=order_result.order_no or "",
            quantity=sizing.quantity,
            price=actual_price,
        )

    def _place_order(self, signal: BuySignal, quantity: int, current_price: int) -> OrderResult:
        """주문 실행 (시장가 or 지정가)."""
        # === DRYRUN 모드: 실주문 대신 가짜 성공 반환 ===
        if self._config.dry_run or self._redis.get(DRYRUN_KEY):
            logger.info(
                "[DRYRUN][%s] BUY %d shares at %d (skipping KIS API)",
                signal.stock_code,
                quantity,
                current_price,
            )
            return OrderResult(
                success=True,
                stock_code=signal.stock_code,
                quantity=quantity,
                price=current_price,
                order_no="DRYRUN-0000",
                message="dryrun",
            )

        config = self._config.scanner

        # Momentum 전략: 지정가 주문
        if config.momentum_limit_order_enabled and signal.signal_type in MOMENTUM_STRATEGIES:
            premium = config.momentum_limit_premium
            limit_price = int(current_price * (1 + premium))
            # KRX 호가 단위 정렬
            limit_price = _align_tick_size(limit_price)

            order = OrderRequest(
                stock_code=signal.stock_code,
                quantity=quantity,
                order_type=OrderType.LIMIT,
                price=limit_price,
            )
            try:
                result = self._kis.buy(order)
                if result.success and result.order_no:
                    # 체결 대기 → 미체결 시 취소
                    time.sleep(config.momentum_limit_timeout_sec)
                    cancelled = self._kis.cancel_order(result.order_no)
                    if cancelled:
                        logger.info("[%s] Limit order cancelled (timeout)", signal.stock_code)
                        return OrderResult(
                            success=False,
                            stock_code=signal.stock_code,
                            quantity=quantity,
                            price=limit_price,
                            message="Limit order timeout",
                        )
                return result
            except Exception as e:
                logger.error("[%s] Limit order failed: %s", signal.stock_code, e)
                return OrderResult(
                    success=False,
                    stock_code=signal.stock_code,
                    quantity=quantity,
                    price=limit_price,
                    message=str(e),
                )

        # 시장가 주문
        order = OrderRequest(
            stock_code=signal.stock_code,
            quantity=quantity,
            order_type=OrderType.MARKET,
        )
        try:
            return self._kis.buy(order)
        except Exception as e:
            logger.error("[%s] Market order failed: %s", signal.stock_code, e)
            return OrderResult(
                success=False,
                stock_code=signal.stock_code,
                quantity=quantity,
                price=current_price,
                message=str(e),
            )

    def _calculate_atr(self, stock_code: str, current_price: int) -> float:
        """ATR 계산 (일봉 기반, 실패 시 2% 폴백)."""
        try:
            daily_prices = self._kis.get_daily_prices(stock_code, days=30)
            if len(daily_prices) >= 2:
                price_dicts = [{"high": p.high_price, "low": p.low_price, "close": p.close_price} for p in daily_prices]
                atr = calculate_atr(price_dicts)
                if atr > 0:
                    return clamp_atr(atr, current_price)
        except Exception:
            logger.debug("[%s] Daily prices fetch failed, using 2%% fallback", stock_code)
        return clamp_atr(current_price * 0.02, current_price)

    def _get_positions(self) -> list[Position] | None:
        """현재 보유 포지션. 실패 시 None (매수 차단용)."""
        try:
            return self._kis.get_positions()
        except Exception:
            logger.error("Failed to get positions — blocking buy")
            return None

    def _get_cash_balance(self) -> int:
        """현금 잔고."""
        try:
            data = self._kis.get_balance()
            return int(data.get("cash_balance", 0))
        except Exception:
            logger.error("Failed to get cash balance")
            return 0

    def _is_emergency_stopped(self) -> bool:
        """Emergency stop 확인."""
        try:
            return bool(self._redis.get(EMERGENCY_STOP_KEY))
        except Exception:
            return False

    def _check_daily_limit(self) -> bool:
        """일일 매수 횟수 제한."""
        try:
            key = f"buy_count:{datetime.now(UTC).strftime('%Y-%m-%d')}"
            count = self._redis.get(key)
            return not (count and int(count) >= self._config.risk.max_buy_count_per_day)
        except Exception:
            return True

    def _increment_buy_count(self) -> None:
        """일일 매수 카운트 증가."""
        try:
            key = f"buy_count:{datetime.now(UTC).strftime('%Y-%m-%d')}"
            pipe = self._redis.pipeline()
            pipe.incr(key)
            pipe.expire(key, 86400)
            pipe.execute()
        except Exception:
            pass

    def _check_correlation(self, stock_code: str, positions: list[Position]) -> tuple[bool, float, str]:
        """보유 종목과 상관관계 체크."""
        from .correlation import check_portfolio_correlation

        try:
            daily = self._kis.get_daily_prices(stock_code, days=60)
            candidate_prices = [p.close_price for p in daily]
        except Exception:
            logger.debug("[%s] Daily prices fetch failed for correlation", stock_code)
            return (True, 0.0, "Price data unavailable")

        def price_lookup(code: str) -> list[float]:
            daily_p = self._kis.get_daily_prices(code, days=60)
            return [p.close_price for p in daily_p]

        return check_portfolio_correlation(
            candidate_code=stock_code,
            candidate_prices=candidate_prices,
            positions=positions,
            price_lookup_fn=price_lookup,
            block_threshold=self._config.risk.correlation_block_threshold,
        )

    def _cleanup_position_state(self, stock_code: str) -> None:
        """매수 시 이전 포지션의 잔여 Redis 상태 초기화."""
        try:
            pipe = self._redis.pipeline()
            pipe.delete(f"watermark:{stock_code}")
            pipe.delete(f"scale_out:{stock_code}")
            pipe.delete(f"rsi_sold:{stock_code}")
            pipe.delete(f"profit_floor:{stock_code}")
            pipe.execute()
        except Exception:
            pass

    def _acquire_lock(self, stock_code: str, ttl: int = 180) -> bool:
        """분산 락 획득."""
        try:
            return bool(self._redis.set(f"{LOCK_PREFIX}{stock_code}", "1", nx=True, ex=ttl))
        except Exception:
            return False

    def _release_lock(self, stock_code: str) -> None:
        """분산 락 해제."""
        with contextlib.suppress(Exception):
            self._redis.delete(f"{LOCK_PREFIX}{stock_code}")


def _align_tick_size(price: int) -> int:
    """KRX 호가 단위 정렬.

    2,000 미만: 1원
    2,000~5,000: 5원
    5,000~20,000: 10원
    20,000~50,000: 50원
    50,000~200,000: 100원
    200,000~500,000: 500원
    500,000 이상: 1,000원
    """
    if price < 2000:
        tick = 1
    elif price < 5000:
        tick = 5
    elif price < 20000:
        tick = 10
    elif price < 50000:
        tick = 50
    elif price < 200000:
        tick = 100
    elif price < 500000:
        tick = 500
    else:
        tick = 1000

    return (price // tick) * tick
