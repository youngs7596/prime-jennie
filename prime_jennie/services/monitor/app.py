"""Price Monitor — 실시간 포지션 감시 + 매도 시그널 발행.

kis:prices Redis Stream에서 실시간 틱을 소비하여
보유 포지션 가격 변동 시 즉시 다층 매도 규칙(exit_rules)을 평가,
SellOrder를 Redis Stream에 발행.

Data Flow:
  KIS WebSocket → Gateway → Redis kis:prices → Monitor (XREADGROUP)
  → exit_rules 평가 → Redis stream:sell-orders → Sell Executor

5분 주기: kis.get_positions() → _positions 갱신 + RSI/ATR/지표 일괄 계산
"""

import contextlib
import logging
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import redis

from prime_jennie.domain.config import get_config
from prime_jennie.domain.enums import MarketRegime, SellReason
from prime_jennie.domain.macro import TradingContext
from prime_jennie.domain.portfolio import Position
from prime_jennie.domain.trading import SellOrder
from prime_jennie.infra.kis.client import KISClient
from prime_jennie.infra.redis.cache import TypedCache
from prime_jennie.infra.redis.client import get_redis
from prime_jennie.infra.redis.streams import TypedStreamPublisher
from prime_jennie.services.base import create_app

from .exit_rules import ExitSignal, PositionContext, evaluate_exit

logger = logging.getLogger(__name__)

# Redis Keys
WATERMARK_PREFIX = "watermark:"
SCALE_OUT_PREFIX = "scale_out:"
RSI_SOLD_PREFIX = "rsi_sold:"
COOLDOWN_PREFIX = "stoploss_cooldown:"
PROFIT_FLOOR_PREFIX = "profit_floor:"
MONITOR_STATUS_KEY = "monitoring:price_monitor"

# Streams
SELL_SIGNAL_STREAM = "stream:sell-orders"
PRICE_STREAM = "kis:prices"
PRICE_GROUP = "monitor-group"
PRICE_CONSUMER = "monitor-1"

# Timing
POSITION_REFRESH_INTERVAL = 300  # 5분마다 포지션 + RSI 갱신
STATUS_LOG_INTERVAL_SEC = 300


@dataclass
class IndicatorCache:
    """종목별 기술적 지표 캐시."""

    macd_bearish: bool = False
    death_cross: bool = False


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
        context_cache: TypedCache[TradingContext] | None = None,
    ):
        self._config = get_config()
        self._kis = kis_client
        self._redis = redis_client
        self._publisher = TypedStreamPublisher(redis_client, SELL_SIGNAL_STREAM, SellOrder)
        self._context_cache = context_cache
        self._last_status_log = 0.0

        # 인메모리 캐시
        self._positions: dict[str, Position] = {}
        self._rsi_cache: dict[str, float | None] = {}
        self._atr_cache: dict[str, float] = {}
        self._indicator_cache: dict[str, IndicatorCache] = {}

    # --- Public API ---

    def refresh_positions(self) -> list[str]:
        """kis.get_positions() → _positions 갱신, RSI/ATR/지표 일괄 계산.

        Returns:
            현재 보유 종목 코드 리스트
        """
        try:
            positions = self._kis.get_positions()
        except Exception as e:
            logger.error("Failed to get positions: %s", e)
            return list(self._positions.keys())

        new_codes = {p.stock_code for p in positions}
        old_codes = set(self._positions.keys())

        # 사라진 포지션 Redis 상태 정리
        for code in old_codes - new_codes:
            self._cleanup_position_state(code)
            self._rsi_cache.pop(code, None)
            self._atr_cache.pop(code, None)
            self._indicator_cache.pop(code, None)

        # _positions 갱신
        self._positions = {p.stock_code: p for p in positions}

        # daily_prices 1회 fetch → RSI + ATR + indicators 일괄 계산
        for code in new_codes:
            self._compute_all_indicators(code)

        logger.info("Positions refreshed: %d held", len(self._positions))
        return list(new_codes)

    def process_tick(self, stock_code: str, price: float, high: float = 0) -> None:
        """보유 종목 틱 → 매도 규칙 평가 → 시그널 발행."""
        pos = self._positions.get(stock_code)
        if pos is None:
            return

        # 현재가 갱신 (인메모리)
        pos = pos.model_copy(update={"current_price": int(price)})
        self._positions[stock_code] = pos

        context = self._get_trading_context()
        regime = context.market_regime if context else MarketRegime.SIDEWAYS
        macro_stop_mult = context.stop_loss_multiplier if context else 1.0

        try:
            signal = self._evaluate_position(pos, regime, macro_stop_mult)
            if signal and signal.should_sell:
                self._emit_sell_order(pos, signal)
        except Exception:
            logger.exception("Evaluation failed for %s", stock_code)

    def get_status(self) -> dict:
        """현재 상태 반환."""
        return {
            "position_count": len(self._positions),
            "positions": list(self._positions.keys()),
            "rsi_cached": len(self._rsi_cache),
            "atr_cached": len(self._atr_cache),
            "indicator_cached": len(self._indicator_cache),
        }

    # --- Position Evaluation ---

    def _evaluate_position(
        self,
        pos: Position,
        regime: MarketRegime,
        macro_stop_mult: float,
    ) -> ExitSignal | None:
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

        # Profit floor 상태 관리
        sell_config = self._config.sell
        profit_floor_active = self._get_profit_floor(pos.stock_code)
        if not profit_floor_active and high_profit_pct >= sell_config.profit_floor_activation:
            profit_floor_active = True
            self._set_profit_floor(pos.stock_code)

        # ATR (실제 계산값, 캐시)
        atr = self._atr_cache.get(pos.stock_code, 0.0)

        # RSI (캐시)
        rsi = self._rsi_cache.get(pos.stock_code)

        # 기술적 지표 (캐시)
        indicators = self._indicator_cache.get(pos.stock_code, IndicatorCache())

        # Holding days
        holding_days = 0
        if pos.bought_at:
            delta = datetime.now(UTC) - pos.bought_at
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
            macd_bearish=indicators.macd_bearish,
            death_cross=indicators.death_cross,
            profit_floor_active=profit_floor_active,
            profit_floor_level=sell_config.profit_floor_level,
        )

        return evaluate_exit(ctx, regime, macro_stop_mult)

    def _emit_sell_order(self, pos: Position, signal: ExitSignal) -> None:
        """매도 시그널 Redis Stream 발행."""
        sell_qty = max(1, int(pos.quantity * signal.quantity_pct / 100))

        # Holding days 계산
        holding_days = None
        if pos.bought_at:
            holding_days = (datetime.now(UTC) - pos.bought_at).days

        order = SellOrder(
            stock_code=pos.stock_code,
            stock_name=pos.stock_name,
            sell_reason=signal.reason,
            current_price=pos.current_price or pos.average_buy_price,
            quantity=sell_qty,
            timestamp=datetime.now(UTC),
            buy_price=pos.average_buy_price,
            profit_pct=round(
                (float(pos.current_price or 0) - float(pos.average_buy_price)) / float(pos.average_buy_price) * 100,
                2,
            )
            if pos.average_buy_price > 0
            else None,
            holding_days=holding_days,
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

        # 전량 매도 시 Redis 상태 정리 + 인메모리 제거
        if signal.quantity_pct >= 100:
            self._cleanup_position_state(pos.stock_code)
            self._positions.pop(pos.stock_code, None)
            self._rsi_cache.pop(pos.stock_code, None)
            self._atr_cache.pop(pos.stock_code, None)
            self._indicator_cache.pop(pos.stock_code, None)

    # --- Trading Context ---

    def _get_trading_context(self) -> TradingContext | None:
        if self._context_cache:
            return self._context_cache.get()
        return None

    # --- All-in-one Indicator Computation ---

    def _compute_all_indicators(self, stock_code: str) -> None:
        """daily_prices 1회 fetch → RSI + ATR + death_cross + MACD 일괄 계산."""
        try:
            daily_prices = self._kis.get_daily_prices(stock_code, days=60)
        except Exception as e:
            logger.warning("[%s] Daily prices fetch failed: %s", stock_code, e)
            self._rsi_cache[stock_code] = None
            self._atr_cache[stock_code] = 0.0
            self._indicator_cache[stock_code] = IndicatorCache()
            return

        close_prices = [float(p.close_price) for p in daily_prices]

        # RSI
        self._rsi_cache[stock_code] = self._compute_rsi_from_prices(close_prices)

        # ATR
        self._atr_cache[stock_code] = self._compute_atr_from_prices(daily_prices)

        # Technical indicators
        indicators = IndicatorCache()
        if len(close_prices) >= 21:
            try:
                from .indicators import check_death_cross, check_macd_bearish_divergence

                indicators.death_cross = check_death_cross(close_prices)
                if len(close_prices) >= 36:
                    indicators.macd_bearish = check_macd_bearish_divergence(close_prices)
            except Exception as e:
                logger.warning("[%s] Indicator computation failed: %s", stock_code, e)
        self._indicator_cache[stock_code] = indicators

    def _compute_rsi_from_prices(self, close_prices: list[float]) -> float | None:
        """종가 리스트로부터 RSI 계산."""
        if len(close_prices) < 15:
            return None
        try:
            from prime_jennie.services.buyer.position_sizing import calculate_rsi

            return calculate_rsi(close_prices)
        except Exception:
            return None

    def _compute_atr_from_prices(self, daily_prices: list) -> float:
        """일봉 데이터로부터 ATR 계산."""
        if len(daily_prices) < 2:
            return 0.0
        try:
            from prime_jennie.services.buyer.position_sizing import calculate_atr

            price_dicts = [{"high": p.high_price, "low": p.low_price, "close": p.close_price} for p in daily_prices]
            return calculate_atr(price_dicts)
        except Exception:
            return 0.0

    # --- High Watermark ---

    def _get_high_watermark(self, stock_code: str, buy_price: float) -> float:
        try:
            raw = self._redis.get(f"{WATERMARK_PREFIX}{stock_code}")
            if raw:
                return float(raw)
        except Exception:
            pass
        return buy_price

    def _set_high_watermark(self, stock_code: str, price: float) -> None:
        with contextlib.suppress(Exception):
            self._redis.setex(
                f"{WATERMARK_PREFIX}{stock_code}",
                30 * 86400,
                str(price),
            )

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
        with contextlib.suppress(Exception):
            self._redis.setex(f"{RSI_SOLD_PREFIX}{stock_code}", 86400, "1")

    # --- Profit Floor ---

    def _get_profit_floor(self, stock_code: str) -> bool:
        try:
            return bool(self._redis.get(f"{PROFIT_FLOOR_PREFIX}{stock_code}"))
        except Exception:
            return False

    def _set_profit_floor(self, stock_code: str) -> None:
        with contextlib.suppress(Exception):
            self._redis.setex(f"{PROFIT_FLOOR_PREFIX}{stock_code}", 60 * 86400, "1")

    # --- Cleanup ---

    def _cleanup_position_state(self, stock_code: str) -> None:
        try:
            pipe = self._redis.pipeline()
            pipe.delete(f"{WATERMARK_PREFIX}{stock_code}")
            pipe.delete(f"{SCALE_OUT_PREFIX}{stock_code}")
            pipe.delete(f"{RSI_SOLD_PREFIX}{stock_code}")
            pipe.delete(f"{PROFIT_FLOOR_PREFIX}{stock_code}")
            pipe.execute()
        except Exception:
            pass

    # --- Status ---

    def _log_status(self) -> None:
        now = time.time()
        if now - self._last_status_log < STATUS_LOG_INTERVAL_SEC:
            return
        self._last_status_log = now

        logger.info(
            "Monitor status: watching %d positions",
            len(self._positions),
        )
        try:
            import json

            self._redis.setex(
                MONITOR_STATUS_KEY,
                60,
                json.dumps(
                    {
                        "status": "online",
                        "watching_count": len(self._positions),
                        "updated_at": datetime.now(UTC).isoformat(),
                    }
                ),
            )
        except Exception:
            pass


# ─── Tick Consumer ─────────────────────────────────────────────

_monitor: PriceMonitor | None = None
_tick_thread: threading.Thread | None = None
_tick_running = False


def _consume_ticks(r: redis.Redis, monitor: PriceMonitor) -> None:
    """kis:prices Redis Stream 소비 루프 (백그라운드 스레드).

    Gateway의 KISWebSocketStreamer가 XADD한 raw tick을 읽어
    monitor.process_tick()에 전달.
    5분마다 refresh_positions() + 신규 종목 gateway 구독.
    """
    global _tick_running

    # Consumer group 생성
    try:
        r.xgroup_create(PRICE_STREAM, PRICE_GROUP, id="0", mkstream=True)
    except redis.exceptions.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise

    logger.info("Tick consumer started: stream=%s group=%s", PRICE_STREAM, PRICE_GROUP)
    last_refresh = time.time()
    tick_count = 0

    while _tick_running:
        try:
            # 주기적 포지션 갱신 + RSI 재계산
            now = time.time()
            if now - last_refresh > POSITION_REFRESH_INTERVAL:
                old_codes = set(monitor._positions.keys())
                codes = monitor.refresh_positions()
                new_codes = set(codes) - old_codes
                if new_codes:
                    _subscribe_to_gateway(list(new_codes))
                last_refresh = now

            messages = r.xreadgroup(
                PRICE_GROUP,
                PRICE_CONSUMER,
                {PRICE_STREAM: ">"},
                count=50,
                block=2000,
            )
            if not messages:
                continue

            for _stream_name, entries in messages:
                for msg_id, data in entries:
                    # ACK first (at-most-once)
                    r.xack(PRICE_STREAM, PRICE_GROUP, msg_id)

                    code = data.get("code", "")
                    price_str = data.get("price", "0")
                    high_str = data.get("high", "0")

                    try:
                        price = float(price_str)
                        high = float(high_str)
                    except (ValueError, TypeError):
                        continue

                    if price > 0 and code:
                        monitor.process_tick(code, price, high)
                        tick_count += 1

            # 주기적 상태 로깅
            monitor._log_status()

            if tick_count > 0 and tick_count % 10000 == 0:
                logger.info("Processed %d ticks", tick_count)

        except redis.exceptions.ConnectionError:
            logger.error("Redis connection lost, retrying in 5s...")
            time.sleep(5)
        except Exception:
            logger.exception("Tick consumer error")
            time.sleep(1)

    logger.info("Tick consumer stopped (processed %d ticks)", tick_count)


def _subscribe_to_gateway(codes: list[str]) -> None:
    """Gateway에 실시간 구독 요청."""
    if not codes:
        return
    config = get_config()
    gateway_url = config.kis.gateway_url
    try:
        resp = httpx.post(
            f"{gateway_url}/api/realtime/subscribe",
            json={"codes": codes},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        logger.info(
            "Gateway subscribe: added=%d, total=%d",
            len(data.get("added", [])),
            data.get("total_subscriptions", 0),
        )
    except Exception as e:
        logger.warning("Failed to subscribe via Gateway (will use existing feed): %s", e)


# ─── FastAPI App ────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app) -> AsyncIterator[None]:
    global _monitor, _tick_thread, _tick_running

    r = get_redis()
    kis = KISClient()
    _monitor = PriceMonitor(kis, r)

    # 초기 포지션 로드 + RSI/ATR/지표 계산
    codes = _monitor.refresh_positions()

    # Gateway에 구독 요청
    if codes:
        _subscribe_to_gateway(codes)

    # Tick consumer 시작
    _tick_running = True
    _tick_thread = threading.Thread(target=_consume_ticks, args=(r, _monitor), daemon=True)
    _tick_thread.start()
    logger.info("Price Monitor started with tick consumer")

    yield

    # 종료
    _tick_running = False
    if _tick_thread:
        _tick_thread.join(timeout=5)
    logger.info("Price Monitor stopped")


app = create_app("price-monitor", version="1.0.0", lifespan=lifespan, dependencies=["redis"])


@app.get("/status")
async def status():
    """Monitor 상태 (tick consumer 포함)."""
    base = _monitor.get_status() if _monitor else {}
    base["tick_consumer_running"] = _tick_running
    return base


@app.post("/refresh-positions")
async def refresh_positions():
    """포지션 수동 갱신 + Gateway 재구독."""
    if _monitor is None:
        return {"success": False, "message": "Monitor not initialized"}

    old_codes = set(_monitor._positions.keys())
    codes = _monitor.refresh_positions()
    new_codes = set(codes) - old_codes
    if new_codes:
        _subscribe_to_gateway(list(new_codes))

    return {
        "success": True,
        "position_count": len(codes),
        "new_subscriptions": list(new_codes),
    }
