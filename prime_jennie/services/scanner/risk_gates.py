"""Risk Gates — 매수 시그널 발행 전 순차 안전 체크.

11개 게이트를 순차적으로 통과해야 시그널 발행 허용.
하나라도 실패하면 즉시 거부 (fail-fast).
"""

from __future__ import annotations

import logging
from datetime import datetime, time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import redis

from prime_jennie.domain.config import ScannerConfig
from prime_jennie.domain.enums import MarketRegime, TradeTier, VixRegime
from prime_jennie.domain.macro import TradingContext

from .bar_engine import Bar

logger = logging.getLogger(__name__)


class GateResult:
    """게이트 체크 결과."""

    __slots__ = ("passed", "gate_name", "reason")

    def __init__(self, passed: bool, gate_name: str, reason: str = ""):
        self.passed = passed
        self.gate_name = gate_name
        self.reason = reason

    def __bool__(self) -> bool:
        return self.passed

    def __repr__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return f"GateResult({status}, {self.gate_name}: {self.reason})"


def _kst_now() -> datetime:
    """현재 KST 시각."""
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Asia/Seoul"))


def _parse_time(s: str) -> time:
    parts = s.split(":")
    return time(int(parts[0]), int(parts[1]))


def check_min_bars(bars: list[Bar], min_required: int = 20) -> GateResult:
    """Gate 1: 최소 바 개수."""
    if len(bars) >= min_required:
        return GateResult(True, "min_bars")
    return GateResult(False, "min_bars", f"Need {min_required} bars, got {len(bars)}")


def check_no_trade_window(config: ScannerConfig, now: datetime | None = None) -> GateResult:
    """Gate 2: 장초 노이즈 구간 (09:00-09:15 기본) 진입 금지."""
    now = now or _kst_now()
    start = _parse_time(config.no_trade_window_start)
    end = _parse_time(config.no_trade_window_end)
    current = now.time()

    if start <= current < end:
        return GateResult(
            False,
            "no_trade_window",
            f"No-trade window {config.no_trade_window_start}-{config.no_trade_window_end}",
        )
    return GateResult(True, "no_trade_window")


def check_danger_zone(config: ScannerConfig, now: datetime | None = None) -> GateResult:
    """Gate 3: 장 후반 위험 구간 (14:00-15:00 기본) 진입 금지."""
    now = now or _kst_now()
    start = _parse_time(config.danger_zone_start)
    end = _parse_time(config.danger_zone_end)
    current = now.time()

    if start <= current < end:
        return GateResult(
            False,
            "danger_zone",
            f"Danger zone {config.danger_zone_start}-{config.danger_zone_end}",
        )
    return GateResult(True, "danger_zone")


def check_rsi_guard(rsi: float | None, max_rsi: float = 75.0) -> GateResult:
    """Gate 4: RSI 과열 체크."""
    if rsi is None:
        return GateResult(True, "rsi_guard", "RSI not available")
    if rsi > max_rsi:
        return GateResult(False, "rsi_guard", f"RSI {rsi:.1f} > {max_rsi}")
    return GateResult(True, "rsi_guard")


def check_macro_risk(context: TradingContext) -> GateResult:
    """Gate 5: 매크로 리스크 체크 (Risk-Off Level / VIX Crisis)."""
    if context.risk_off_level >= 2:
        return GateResult(
            False,
            "macro_risk",
            f"Risk-Off Level {context.risk_off_level}",
        )
    if context.vix_regime == VixRegime.CRISIS:
        return GateResult(False, "macro_risk", "VIX Crisis")
    return GateResult(True, "macro_risk")


def check_market_regime(regime: MarketRegime, block_bear: bool = True) -> GateResult:
    """Gate 6: BEAR/STRONG_BEAR 진입 차단."""
    if not block_bear:
        return GateResult(True, "market_regime")
    if regime in (MarketRegime.BEAR, MarketRegime.STRONG_BEAR):
        return GateResult(False, "market_regime", f"Bear market: {regime}")
    return GateResult(True, "market_regime")


def check_combined_risk(
    volume_ratio: float,
    vwap: float,
    current_price: float,
    volume_limit: float = 2.0,
    vwap_deviation_limit: float = 0.02,
) -> GateResult:
    """Gate 7: 복합 리스크 (거래량 급증 + VWAP 이격 동시 → 차단)."""
    risk_count = 0

    if volume_ratio > volume_limit:
        risk_count += 1
    if vwap > 0 and current_price > vwap * (1 + vwap_deviation_limit):
        risk_count += 1

    if risk_count >= 2:
        return GateResult(
            False,
            "combined_risk",
            f"Volume ratio {volume_ratio:.1f}x + VWAP dev {((current_price / vwap - 1) * 100):.1f}%",
        )
    return GateResult(True, "combined_risk")


def check_cooldown(
    stock_code: str,
    last_signal_times: dict[str, float],
    cooldown_sec: int = 600,
) -> GateResult:
    """Gate 8: 동일 종목 재진입 쿨다운."""
    import time as t

    last_time = last_signal_times.get(stock_code)
    if last_time is None:
        return GateResult(True, "cooldown")

    elapsed = t.time() - last_time
    if elapsed < cooldown_sec:
        remaining = int(cooldown_sec - elapsed)
        return GateResult(False, "cooldown", f"Cooldown: {remaining}s remaining")
    return GateResult(True, "cooldown")


def check_stoploss_cooldown(
    stock_code: str,
    redis_client: redis.Redis | None = None,
) -> GateResult:
    """Gate 8-1: 손절 후 재매수 쿨다운 (Redis 기반, 서비스 재시작에도 유지)."""
    if redis_client is None:
        return GateResult(True, "stoploss_cooldown")
    try:
        ttl = redis_client.ttl(f"stoploss_cooldown:{stock_code}")
        if ttl and ttl > 0:
            days_left = round(ttl / 86400, 1)
            return GateResult(
                False,
                "stoploss_cooldown",
                f"Stop-loss cooldown: {days_left}d remaining",
            )
    except Exception:
        pass
    return GateResult(True, "stoploss_cooldown")


def check_sell_cooldown(
    stock_code: str,
    redis_client: redis.Redis | None = None,
) -> GateResult:
    """Gate 8-2: 매도 후 24h 재매수 쿨다운."""
    if redis_client is None:
        return GateResult(True, "sell_cooldown")
    try:
        ttl = redis_client.ttl(f"sell_cooldown:{stock_code}")
        if ttl and ttl > 0:
            hours_left = round(ttl / 3600, 1)
            return GateResult(
                False,
                "sell_cooldown",
                f"Sell cooldown: {hours_left}h remaining",
            )
    except Exception:
        pass
    return GateResult(True, "sell_cooldown")


def check_trade_tier(trade_tier: TradeTier) -> GateResult:
    """Gate 9: BLOCKED 티어 종목 차단."""
    if trade_tier == TradeTier.BLOCKED:
        return GateResult(False, "trade_tier", "BLOCKED tier (Scout Veto)")
    return GateResult(True, "trade_tier")


def check_micro_timing(bars: list[Bar]) -> GateResult:
    """Gate 10: 미시 타이밍 체크 (Shooting Star, Bearish Engulfing)."""
    if len(bars) < 2:
        return GateResult(True, "micro_timing", "Not enough bars")

    last = bars[-1]
    prev = bars[-2]

    # Shooting Star: 긴 윗꼬리 + 작은 몸통
    body = abs(last.close - last.open)
    upper_shadow = last.high - max(last.close, last.open)
    if body > 0 and upper_shadow > body * 2:
        return GateResult(False, "micro_timing", "Shooting Star pattern")

    # Bearish Engulfing: 이전 양봉을 완전히 감싸는 음봉
    prev_bullish = prev.close > prev.open
    curr_bearish = last.close < last.open
    if prev_bullish and curr_bearish and last.open >= prev.close and last.close <= prev.open:
        return GateResult(False, "micro_timing", "Bearish Engulfing pattern")

    return GateResult(True, "micro_timing")


def run_all_gates(
    stock_code: str,
    bars: list[Bar],
    current_price: float,
    rsi: float | None,
    volume_ratio: float,
    vwap: float,
    trade_tier: TradeTier,
    context: TradingContext,
    config: ScannerConfig,
    last_signal_times: dict[str, float],
    redis_client: redis.Redis | None = None,
) -> GateResult:
    """모든 게이트 순차 실행. 첫 번째 실패 시 즉시 반환."""
    gates = [
        lambda: check_min_bars(bars, config.min_required_bars),
        lambda: check_no_trade_window(config),
        lambda: check_danger_zone(config),
        lambda: check_rsi_guard(
            rsi,
            config.rsi_guard_bull_max
            if context.market_regime in (MarketRegime.BULL, MarketRegime.STRONG_BULL)
            else config.rsi_guard_max,
        ),
        lambda: check_macro_risk(context),
        lambda: check_market_regime(context.market_regime),
        lambda: check_combined_risk(
            volume_ratio,
            vwap,
            current_price,
            config.volume_ratio_warning,
            config.vwap_deviation_warning,
        ),
        lambda: check_cooldown(
            stock_code,
            last_signal_times,
            config.signal_cooldown_seconds,
        ),
        lambda: check_stoploss_cooldown(stock_code, redis_client),
        lambda: check_sell_cooldown(stock_code, redis_client),
        lambda: check_trade_tier(trade_tier),
        lambda: check_micro_timing(bars),
    ]

    for gate_fn in gates:
        result = gate_fn()
        if not result:
            logger.debug(
                "[%s] Gate FAIL: %s — %s",
                stock_code,
                result.gate_name,
                result.reason,
            )
            return result

    return GateResult(True, "all_gates", "All gates passed")
