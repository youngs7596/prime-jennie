"""Price Monitor — 실시간 포지션 감시 + 매도 시그널 발행.

KIS Gateway에서 보유 종목 가격을 주기적으로 폴링하고,
다층 매도 규칙(exit_rules)을 평가하여 SellOrder를 Redis Stream에 발행.
"""

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import redis

from prime_jennie.domain.config import get_config
from prime_jennie.domain.enums import MarketRegime, SellReason
from prime_jennie.domain.macro import TradingContext
from prime_jennie.domain.portfolio import Position
from prime_jennie.domain.trading import SellOrder
from prime_jennie.infra.kis.client import KISClient
from prime_jennie.infra.redis.cache import TypedCache
from prime_jennie.infra.redis.streams import TypedStreamPublisher

from .exit_rules import ExitSignal, PositionContext, evaluate_exit

logger = logging.getLogger(__name__)

# Redis Keys
WATERMARK_PREFIX = "watermark:"
SCALE_OUT_PREFIX = "scale_out:"
RSI_SOLD_PREFIX = "rsi_sold:"
COOLDOWN_PREFIX = "stoploss_cooldown:"
MONITOR_STATUS_KEY = "monitoring:price_monitor"

# Stream
SELL_SIGNAL_STREAM = "stream:sell-orders"

# Timing
POLL_INTERVAL_SEC = 30
STATUS_LOG_INTERVAL_SEC = 300  # 5 minutes


class PriceMonitor:
    """포지션 실시간 감시 엔진.

    Args:
        kis_client: KIS Gateway 클라이언트
        redis_client: Redis 클라이언트
        context_cache: 트레이딩 컨텍스트 캐시 (optional)
    """

    def __init__(
        self,
        kis_client: KISClient,
        redis_client: redis.Redis,
        context_cache: Optional[TypedCache[TradingContext]] = None,
    ):
        self._config = get_config()
        self._kis = kis_client
        self._redis = redis_client
        self._publisher = TypedStreamPublisher(
            redis_client, SELL_SIGNAL_STREAM, SellOrder
        )
        self._context_cache = context_cache
        self._stop_event = threading.Event()
        self._last_status_log = 0.0

    def run(self) -> None:
        """메인 모니터링 루프 (blocking)."""
        logger.info("Price Monitor started")

        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception:
                logger.exception("Monitor tick failed")

            self._stop_event.wait(timeout=POLL_INTERVAL_SEC)

        logger.info("Price Monitor stopped")

    def stop(self) -> None:
        """모니터링 중지."""
        self._stop_event.set()

    def _tick(self) -> None:
        """한 사이클: 포지션 조회 → 가격 평가 → 시그널 발행."""
        positions = self._get_positions()
        if not positions:
            return

        context = self._get_trading_context()
        regime = context.market_regime if context else MarketRegime.SIDEWAYS
        macro_stop_mult = context.stop_loss_multiplier if context else 1.0

        for pos in positions:
            try:
                signal = self._evaluate_position(pos, regime, macro_stop_mult)
                if signal and signal.should_sell:
                    self._emit_sell_order(pos, signal)
            except Exception:
                logger.exception("Evaluation failed for %s", pos.stock_code)

        # 상태 로깅
        now = time.time()
        if now - self._last_status_log >= STATUS_LOG_INTERVAL_SEC:
            self._log_status(positions)
            self._last_status_log = now

    def _evaluate_position(
        self,
        pos: Position,
        regime: MarketRegime,
        macro_stop_mult: float,
    ) -> Optional[ExitSignal]:
        """포지션 매도 조건 평가."""
        if pos.current_price is None or pos.current_price <= 0:
            return None

        price = float(pos.current_price)
        buy = float(pos.average_buy_price)
        if buy <= 0:
            return None

        profit_pct = (price - buy) / buy * 100.0

        # High watermark
        hw = self._get_high_watermark(pos.stock_code, buy)
        if price > hw:
            hw = price
            self._set_high_watermark(pos.stock_code, hw)

        high_profit_pct = (hw - buy) / buy * 100.0 if buy > 0 else 0.0

        # ATR & RSI from daily prices
        atr = float(pos.current_price) * 0.02 if pos.current_price else buy * 0.02
        rsi = self._compute_rsi(pos.stock_code)

        # Holding days
        holding_days = 0
        if pos.bought_at:
            delta = datetime.now(timezone.utc) - pos.bought_at
            holding_days = delta.days

        ctx = PositionContext(
            stock_code=pos.stock_code,
            current_price=price,
            buy_price=buy,
            quantity=pos.quantity,
            profit_pct=profit_pct,
            high_watermark=hw,
            high_profit_pct=high_profit_pct,
            atr=atr,
            rsi=rsi,
            holding_days=holding_days,
            scale_out_level=self._get_scale_out_level(pos.stock_code),
            rsi_sold=self._is_rsi_sold(pos.stock_code),
        )

        return evaluate_exit(ctx, regime, macro_stop_mult)

    def _emit_sell_order(self, pos: Position, signal: ExitSignal) -> None:
        """매도 시그널 Redis Stream 발행."""
        sell_qty = max(1, int(pos.quantity * signal.quantity_pct / 100))

        order = SellOrder(
            stock_code=pos.stock_code,
            stock_name=pos.stock_name,
            sell_reason=signal.reason,
            current_price=pos.current_price or pos.average_buy_price,
            quantity=sell_qty,
            timestamp=datetime.now(timezone.utc),
            buy_price=pos.average_buy_price,
            profit_pct=round(
                (float(pos.current_price or 0) - float(pos.average_buy_price))
                / float(pos.average_buy_price)
                * 100,
                2,
            )
            if pos.average_buy_price > 0
            else None,
            holding_days=None,
        )

        self._publisher.publish(order)
        logger.info(
            "[%s] SELL signal: %s qty=%d (%s)",
            pos.stock_code,
            signal.reason,
            sell_qty,
            signal.description,
        )

        # 스케일아웃 레벨 업데이트
        if signal.reason == SellReason.PROFIT_TARGET and signal.quantity_pct < 100:
            self._increment_scale_out_level(pos.stock_code)

        # RSI 매도 플래그
        if signal.reason == SellReason.RSI_OVERBOUGHT:
            self._set_rsi_sold(pos.stock_code)

        # 전량 매도 시 Redis 상태 정리
        if signal.quantity_pct >= 100:
            self._cleanup_position_state(pos.stock_code)

    # --- Position Data ---

    def _get_positions(self) -> list[Position]:
        """보유 포지션 목록 (가격 포함)."""
        try:
            return self._kis.get_positions()
        except Exception:
            logger.error("Failed to get positions")
            return []

    def _get_trading_context(self) -> Optional[TradingContext]:
        """트레이딩 컨텍스트."""
        if self._context_cache:
            return self._context_cache.get()
        return None

    # --- RSI Computation ---

    def _compute_rsi(self, stock_code: str) -> float | None:
        """일봉 종가 기반 RSI 계산 (14-period). 실패 시 None."""
        try:
            from prime_jennie.services.buyer.position_sizing import calculate_rsi

            daily_prices = self._kis.get_daily_prices(stock_code, days=30)
            if len(daily_prices) < 15:
                return None
            close_prices = [float(p.close_price) for p in daily_prices]
            return calculate_rsi(close_prices)
        except Exception:
            logger.debug("[%s] RSI computation failed", stock_code)
            return None

    # --- High Watermark ---

    def _get_high_watermark(self, stock_code: str, buy_price: float) -> float:
        """보유 종목 최고가 조회 (Redis)."""
        try:
            raw = self._redis.get(f"{WATERMARK_PREFIX}{stock_code}")
            if raw:
                return float(raw)
        except Exception:
            pass
        return buy_price

    def _set_high_watermark(self, stock_code: str, price: float) -> None:
        """최고가 갱신."""
        try:
            self._redis.setex(
                f"{WATERMARK_PREFIX}{stock_code}",
                30 * 86400,  # 30 days TTL
                str(price),
            )
        except Exception:
            pass

    # --- Scale-Out Level ---

    def _get_scale_out_level(self, stock_code: str) -> int:
        try:
            raw = self._redis.get(f"{SCALE_OUT_PREFIX}{stock_code}")
            if raw:
                return int(raw)
        except Exception:
            pass
        return 0

    def _increment_scale_out_level(self, stock_code: str) -> None:
        try:
            self._redis.incr(f"{SCALE_OUT_PREFIX}{stock_code}")
            self._redis.expire(f"{SCALE_OUT_PREFIX}{stock_code}", 30 * 86400)
        except Exception:
            pass

    # --- RSI Sold ---

    def _is_rsi_sold(self, stock_code: str) -> bool:
        try:
            return bool(self._redis.get(f"{RSI_SOLD_PREFIX}{stock_code}"))
        except Exception:
            return False

    def _set_rsi_sold(self, stock_code: str) -> None:
        try:
            self._redis.setex(f"{RSI_SOLD_PREFIX}{stock_code}", 86400, "1")
        except Exception:
            pass

    # --- Cleanup ---

    def _cleanup_position_state(self, stock_code: str) -> None:
        """전량 매도 시 Redis 상태 정리."""
        try:
            pipe = self._redis.pipeline()
            pipe.delete(f"{WATERMARK_PREFIX}{stock_code}")
            pipe.delete(f"{SCALE_OUT_PREFIX}{stock_code}")
            pipe.delete(f"{RSI_SOLD_PREFIX}{stock_code}")
            pipe.execute()
        except Exception:
            pass

    # --- Status ---

    def _log_status(self, positions: list[Position]) -> None:
        """상태 로깅."""
        logger.info(
            "Monitor status: watching %d positions",
            len(positions),
        )
        try:
            import json

            self._redis.setex(
                MONITOR_STATUS_KEY,
                60,
                json.dumps(
                    {
                        "status": "online",
                        "watching_count": len(positions),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                ),
            )
        except Exception:
            pass


# --- FastAPI App ---


def create_monitor_app():
    """Price Monitor FastAPI 앱."""
    from prime_jennie.services.base import create_app

    app = create_app("price-monitor", version="1.0.0")

    _monitor: Optional[PriceMonitor] = None
    _thread: Optional[threading.Thread] = None

    @app.post("/start")
    def start_monitoring():
        nonlocal _monitor, _thread
        if _thread and _thread.is_alive():
            return {"status": "already_running"}

        r = redis.Redis.from_url(get_config().redis.url, decode_responses=True)
        kis = KISClient()
        _monitor = PriceMonitor(kis, r)
        _thread = threading.Thread(target=_monitor.run, daemon=True)
        _thread.start()
        return {"status": "started"}

    @app.post("/stop")
    def stop_monitoring():
        nonlocal _monitor
        if _monitor:
            _monitor.stop()
        return {"status": "stopped"}

    @app.get("/status")
    def monitor_status():
        return {
            "running": _thread.is_alive() if _thread else False,
        }

    return app


app = create_monitor_app()
