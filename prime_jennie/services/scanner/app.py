"""Buy Scanner 서비스 — 실시간 매수 기회 감시.

Watchlist 종목을 모니터링하며 매수 시그널을 감지 → Redis Stream 발행.

Data Flow:
  Redis watchlist:active → Scanner → Redis stream:buy-signals → Executor
"""

import logging
import time
from datetime import datetime, timezone

import redis

from prime_jennie.domain import BuySignal, HotWatchlist, TradingContext
from prime_jennie.domain.config import get_config
from prime_jennie.domain.enums import MOMENTUM_STRATEGIES, MarketRegime, SignalType
from prime_jennie.infra.redis.cache import TypedCache
from prime_jennie.infra.redis.streams import TypedStreamPublisher
from prime_jennie.services.base import create_app

from .bar_engine import BarEngine
from .risk_gates import run_all_gates
from .strategies import compute_rsi_from_bars, detect_strategies

logger = logging.getLogger(__name__)

# Stream/Cache 키
STREAM_BUY_SIGNALS = "stream:buy-signals"
CACHE_WATCHLIST = "watchlist:active"
CACHE_TRADING_CONTEXT = "trading:context"


class BuyScanner:
    """매수 기회 감시 엔진.

    Args:
        redis_client: Redis 클라이언트
        bar_engine: 바 집계 엔진 (주입 가능)
    """

    def __init__(
        self,
        redis_client: redis.Redis,
        bar_engine: BarEngine | None = None,
    ):
        self._config = get_config()
        self._redis = redis_client
        self._bar_engine = bar_engine or BarEngine()
        self._publisher = TypedStreamPublisher(
            redis_client, STREAM_BUY_SIGNALS, BuySignal
        )
        self._watchlist_cache = TypedCache(
            redis_client, CACHE_WATCHLIST, HotWatchlist
        )
        self._context_cache = TypedCache(
            redis_client, CACHE_TRADING_CONTEXT, TradingContext
        )

        # 상태
        self._watchlist: HotWatchlist | None = None
        self._context: TradingContext = TradingContext.default()
        self._last_signal_times: dict[str, float] = {}
        self._pending_momentum: dict[str, dict] = {}

    def load_watchlist(self) -> bool:
        """Redis에서 watchlist 로드."""
        wl = self._watchlist_cache.get()
        if wl is None:
            logger.warning("No watchlist found in Redis")
            return False
        self._watchlist = wl
        logger.info(
            "Loaded watchlist: %d stocks, regime=%s",
            len(wl.stocks),
            wl.market_regime,
        )
        return True

    def load_context(self) -> None:
        """Redis에서 TradingContext 로드."""
        ctx = self._context_cache.get()
        if ctx:
            self._context = ctx
        else:
            self._context = TradingContext.default()
            logger.info("Using default TradingContext")

    @property
    def watchlist(self) -> HotWatchlist | None:
        return self._watchlist

    @property
    def context(self) -> TradingContext:
        return self._context

    def process_tick(
        self, stock_code: str, price: float, volume: int = 0
    ) -> BuySignal | None:
        """틱 데이터 처리 → 시그널 발행.

        Returns:
            BuySignal if signal detected and published, else None.
        """
        if self._watchlist is None:
            return None

        entry = self._watchlist.get_stock(stock_code)
        if entry is None:
            return None

        # 바 업데이트
        completed = self._bar_engine.update(stock_code, price, volume)

        # Pending momentum 확인
        if stock_code in self._pending_momentum and completed:
            return self._check_pending_momentum(stock_code, price)

        # 바 완성 시에만 전략 감지
        if completed is None:
            return None

        bars = self._bar_engine.get_recent_bars(stock_code, 30)
        vwap = self._bar_engine.get_vwap(stock_code)
        vol_info = self._bar_engine.get_volume_info(stock_code)
        rsi = compute_rsi_from_bars(bars)
        regime = self._watchlist.market_regime

        # Conviction Entry는 risk gate 우회
        from .strategies import detect_conviction_entry

        conv = detect_conviction_entry(
            bars, entry, price, vwap, rsi, regime, self._config.scanner
        )
        if conv.detected:
            return self._publish_signal(
                stock_code, entry, conv.signal_type, price, rsi, vol_info["ratio"], vwap
            )

        # 나머지 전략은 risk gate 통과 필요
        gate_result = run_all_gates(
            stock_code=stock_code,
            bars=bars,
            current_price=price,
            rsi=rsi,
            volume_ratio=vol_info["ratio"],
            vwap=vwap,
            trade_tier=entry.trade_tier,
            context=self._context,
            config=self._config.scanner,
            last_signal_times=self._last_signal_times,
        )
        if not gate_result:
            return None

        # 전략 감지 (conviction 제외)
        strategy = detect_strategies(
            bars=bars,
            regime=regime,
            entry=entry,
            current_price=price,
            rsi=rsi,
            volume_ratio=vol_info["ratio"],
            vwap=vwap,
            config=self._config.scanner,
        )
        if strategy is None or not strategy.detected:
            return None

        # Momentum 확인 바
        if (
            self._config.scanner.momentum_confirmation_bars > 0
            and strategy.signal_type in MOMENTUM_STRATEGIES
        ):
            self._pending_momentum[stock_code] = {
                "signal_type": strategy.signal_type,
                "initial_price": price,
                "entry": entry,
                "rsi": rsi,
                "volume_ratio": vol_info["ratio"],
                "vwap": vwap,
                "created_at": time.time(),
                "bars_waited": 0,
            }
            logger.info(
                "[%s] Momentum pending: %s at %d",
                stock_code,
                strategy.signal_type,
                price,
            )
            return None

        return self._publish_signal(
            stock_code, entry, strategy.signal_type, price, rsi, vol_info["ratio"], vwap
        )

    def _check_pending_momentum(
        self, stock_code: str, current_price: float
    ) -> BuySignal | None:
        """Pending momentum 확인 → 발행 or 폐기."""
        pending = self._pending_momentum.get(stock_code)
        if pending is None:
            return None

        pending["bars_waited"] += 1
        initial_price = pending["initial_price"]

        # 가격 유지 확인 (시그널 가격 이상)
        if current_price >= initial_price:
            # 확인 완료 → 발행
            del self._pending_momentum[stock_code]
            entry = pending["entry"]
            return self._publish_signal(
                stock_code,
                entry,
                pending["signal_type"],
                current_price,
                pending["rsi"],
                pending["volume_ratio"],
                pending["vwap"],
            )

        # 확인 실패 → 폐기
        if pending["bars_waited"] >= self._config.scanner.momentum_confirmation_bars:
            logger.info(
                "[%s] Momentum discarded: price fell %d → %d",
                stock_code,
                initial_price,
                current_price,
            )
            del self._pending_momentum[stock_code]

        return None

    def _publish_signal(
        self,
        stock_code: str,
        entry,
        signal_type: SignalType,
        price: float,
        rsi: float | None,
        volume_ratio: float,
        vwap: float,
    ) -> BuySignal:
        """BuySignal 생성 및 Redis Stream 발행."""
        signal = BuySignal(
            stock_code=stock_code,
            stock_name=entry.stock_name,
            signal_type=signal_type,
            signal_price=int(price),
            llm_score=entry.llm_score,
            hybrid_score=entry.hybrid_score,
            is_tradable=entry.is_tradable,
            trade_tier=entry.trade_tier,
            risk_tag=entry.risk_tag,
            market_regime=self._watchlist.market_regime,
            source="scanner",
            timestamp=datetime.now(timezone.utc),
            rsi_value=rsi,
            volume_ratio=volume_ratio,
            vwap=vwap,
            position_multiplier=self._context.position_multiplier,
        )

        self._publisher.publish(signal)
        self._last_signal_times[stock_code] = time.time()

        logger.info(
            "[%s] Signal published: %s at %d (hybrid=%.1f)",
            stock_code,
            signal_type,
            price,
            entry.hybrid_score,
        )
        return signal

    def get_status(self) -> dict:
        """현재 상태 반환."""
        return {
            "watchlist_loaded": self._watchlist is not None,
            "stock_count": len(self._watchlist.stocks) if self._watchlist else 0,
            "market_regime": (
                self._watchlist.market_regime if self._watchlist else "UNKNOWN"
            ),
            "pending_momentum": len(self._pending_momentum),
            "active_cooldowns": len(self._last_signal_times),
        }


# ─── FastAPI App ────────────────────────────────────────────────


app = create_app("buy-scanner", version="1.0.0", dependencies=["redis"])


@app.get("/status")
async def status():
    """Scanner 상태 조회."""
    return {"status": "ready", "message": "Scanner requires external tick feed"}
