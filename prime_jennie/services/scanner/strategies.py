"""Strategy Detection — 매수 전략 패턴 감지.

각 전략 함수는 bars + 컨텍스트를 받아 (detected: bool, reason: str) 반환.
"""

import logging
from datetime import datetime, timezone

from prime_jennie.domain.config import ScannerConfig
from prime_jennie.domain.enums import MarketRegime, SignalType
from prime_jennie.domain.watchlist import WatchlistEntry

from .bar_engine import Bar

logger = logging.getLogger(__name__)


class StrategyResult:
    """전략 감지 결과."""

    __slots__ = ("detected", "signal_type", "reason")

    def __init__(
        self,
        detected: bool,
        signal_type: SignalType | None = None,
        reason: str = "",
    ):
        self.detected = detected
        self.signal_type = signal_type
        self.reason = reason


def _compute_sma(prices: list[float], period: int) -> float | None:
    """단순 이동평균."""
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period


def _compute_rsi(closes: list[float], period: int = 14) -> float | None:
    """EMA 기반 RSI."""
    if len(closes) < period + 1:
        return None

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0) for d in deltas]
    losses = [abs(min(d, 0)) for d in deltas]

    # 초기 SMA
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # EMA smoothing
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def compute_rsi_from_bars(bars: list[Bar], period: int = 14) -> float | None:
    """바 리스트에서 RSI 계산."""
    closes = [b.close for b in bars]
    return _compute_rsi(closes, period)


def detect_golden_cross(
    bars: list[Bar],
    short_period: int = 5,
    long_period: int = 20,
    min_volume_ratio: float = 1.5,
    volume_ratio: float = 1.0,
) -> StrategyResult:
    """GOLDEN_CROSS: 단기 MA가 장기 MA를 상향 돌파."""
    if len(bars) < long_period + 1:
        return StrategyResult(False)

    closes = [b.close for b in bars]
    ma_short = _compute_sma(closes, short_period)
    ma_long = _compute_sma(closes, long_period)

    # 이전 바 기준
    prev_closes = closes[:-1]
    prev_short = _compute_sma(prev_closes, short_period)
    prev_long = _compute_sma(prev_closes, long_period)

    if None in (ma_short, ma_long, prev_short, prev_long):
        return StrategyResult(False)

    # 교차: 이전에는 아래, 현재는 위
    crossed = prev_short <= prev_long and ma_short > ma_long
    if not crossed:
        return StrategyResult(False)

    if volume_ratio < min_volume_ratio:
        return StrategyResult(False)

    return StrategyResult(
        True,
        SignalType.GOLDEN_CROSS,
        f"MA{short_period} crossed MA{long_period}, vol_ratio={volume_ratio:.1f}x",
    )


def detect_rsi_rebound(
    bars: list[Bar],
    regime: MarketRegime,
    rsi_threshold: float | None = None,
) -> StrategyResult:
    """RSI_REBOUND: RSI가 과매도 구간에서 반등.

    Bull 국면에서는 비활성화 (역추세 전략).
    """
    if regime in (MarketRegime.BULL, MarketRegime.STRONG_BULL):
        return StrategyResult(False)

    if len(bars) < 16:  # RSI 계산에 최소 15개 필요
        return StrategyResult(False)

    closes = [b.close for b in bars]

    # RSI threshold: 국면별 동적
    if rsi_threshold is None:
        rsi_threshold = {
            MarketRegime.SIDEWAYS: 40.0,
            MarketRegime.BEAR: 30.0,
            MarketRegime.STRONG_BEAR: 25.0,
        }.get(regime, 35.0)

    curr_rsi = _compute_rsi(closes, 14)
    prev_rsi = _compute_rsi(closes[:-1], 14)

    if curr_rsi is None or prev_rsi is None:
        return StrategyResult(False)

    # 이전에 과매도, 현재 반등
    if prev_rsi < rsi_threshold <= curr_rsi:
        return StrategyResult(
            True,
            SignalType.RSI_REBOUND,
            f"RSI rebound: {prev_rsi:.1f} → {curr_rsi:.1f} (threshold={rsi_threshold})",
        )

    return StrategyResult(False)


def detect_momentum(
    bars: list[Bar],
    min_momentum_pct: float = 1.5,
    max_gain_pct: float = 7.0,
) -> StrategyResult:
    """MOMENTUM: 단기 가격 모멘텀.

    추격매수 방지: max_gain_pct 초과 시 비활성화.
    """
    if len(bars) < 5:
        return StrategyResult(False)

    recent = bars[-5:]
    momentum_pct = (recent[-1].close / recent[0].open - 1) * 100

    if momentum_pct < min_momentum_pct:
        return StrategyResult(False)

    if momentum_pct > max_gain_pct:
        return StrategyResult(
            False,
            reason=f"Momentum {momentum_pct:.1f}% > cap {max_gain_pct}% (chase prevention)",
        )

    return StrategyResult(
        True,
        SignalType.MOMENTUM,
        f"Momentum +{momentum_pct:.1f}%",
    )


def detect_momentum_continuation(
    bars: list[Bar],
    regime: MarketRegime,
    llm_score: float = 0,
    max_gain_pct: float = 5.0,
) -> StrategyResult:
    """MOMENTUM_CONTINUATION: Bull 국면 모멘텀 연속.

    MA5 > MA20 + 가격변화 2-5% + LLM >= 65.
    """
    if regime not in (MarketRegime.BULL, MarketRegime.STRONG_BULL):
        return StrategyResult(False)

    if len(bars) < 21:
        return StrategyResult(False)

    closes = [b.close for b in bars]
    ma5 = _compute_sma(closes, 5)
    ma20 = _compute_sma(closes, 20)

    if ma5 is None or ma20 is None or ma5 <= ma20:
        return StrategyResult(False)

    price_change = (closes[-1] / closes[-5] - 1) * 100 if closes[-5] > 0 else 0
    if price_change < 2.0 or price_change > max_gain_pct:
        return StrategyResult(False)

    if llm_score < 65:
        return StrategyResult(False)

    return StrategyResult(
        True,
        SignalType.MOMENTUM_CONTINUATION,
        f"Continuation: MA5>MA20, change={price_change:.1f}%, LLM={llm_score}",
    )


def detect_dip_buy(
    stock_code: str,
    bars: list[Bar],
    entry: WatchlistEntry,
    regime: MarketRegime,
    max_days: int = 5,
) -> StrategyResult:
    """DIP_BUY: Watchlist 진입 후 눌림목 매수.

    조건: scored_at D+1~5, 가격 조정 구간.
    """
    if len(bars) < 5:
        return StrategyResult(False)

    if entry.scored_at is None:
        return StrategyResult(False)

    now = datetime.now(timezone.utc)
    days_since = (now - entry.scored_at).days
    if days_since < 1 or days_since > max_days:
        return StrategyResult(False)

    # 최근 5개 바의 고점 대비 하락
    recent = bars[-5:]
    high = max(b.high for b in recent)
    current = recent[-1].close
    dip_pct = (current / high - 1) * 100

    # 국면별 조정 범위
    if regime in (MarketRegime.BULL, MarketRegime.STRONG_BULL):
        min_dip, max_dip = -0.5, -3.0
    else:
        min_dip, max_dip = -2.0, -5.0

    if max_dip <= dip_pct <= min_dip:
        return StrategyResult(
            True,
            SignalType.DIP_BUY,
            f"Dip {dip_pct:.1f}% in {days_since}d (range [{max_dip}, {min_dip}])",
        )

    return StrategyResult(False)


def detect_conviction_entry(
    bars: list[Bar],
    entry: WatchlistEntry,
    current_price: float,
    vwap: float,
    rsi: float | None,
    regime: MarketRegime,
    config: ScannerConfig,
) -> StrategyResult:
    """CONVICTION_ENTRY: Scout 고확신 종목 장 초반 선제 매수.

    Risk Gate 우회. 조건:
    1. BULL/STRONG_BULL (또는 SIDEWAYS + hybrid>=75)
    2. Watchlist D+0~2
    3. hybrid>=70 OR llm>=72
    4. Time 09:15-10:30 KST
    5. Intraday gain < 3%
    6. VWAP deviation < 1.5%
    7. RSI < 65
    """
    if not config.conviction_entry_enabled:
        return StrategyResult(False)

    if entry.trade_tier == "BLOCKED":
        return StrategyResult(False)

    # 1. Regime check
    if regime in (MarketRegime.BEAR, MarketRegime.STRONG_BEAR):
        return StrategyResult(False)
    if regime == MarketRegime.SIDEWAYS and entry.hybrid_score < 75:
        return StrategyResult(False)

    # 2. Age check (D+0~2)
    if entry.scored_at is not None:
        now = datetime.now(timezone.utc)
        days = (now - entry.scored_at).days
        if days > 2:
            return StrategyResult(False)

    # 3. Confidence check
    has_high_hybrid = entry.hybrid_score >= config.conviction_min_hybrid_score
    has_high_llm = entry.llm_score >= config.conviction_min_llm_score
    if not (has_high_hybrid or has_high_llm):
        return StrategyResult(False)

    # 4. Time window check (KST)
    from .risk_gates import _kst_now, _parse_time

    now_kst = _kst_now()
    win_start = _parse_time(config.conviction_window_start)
    win_end = _parse_time(config.conviction_window_end)
    if not (win_start <= now_kst.time() <= win_end):
        return StrategyResult(False)

    # 5. Intraday gain check
    if len(bars) >= 2:
        open_price = bars[0].open
        if open_price > 0:
            gain_pct = (current_price / open_price - 1) * 100
            if gain_pct >= config.conviction_max_gain_pct:
                return StrategyResult(False)

    # 6. VWAP deviation check
    if vwap > 0:
        vwap_dev = abs(current_price / vwap - 1) * 100
        if vwap_dev > 1.5:
            return StrategyResult(False)

    # 7. RSI check
    if rsi is not None and rsi >= 65:
        return StrategyResult(False)

    return StrategyResult(
        True,
        SignalType.WATCHLIST_CONVICTION,
        f"Conviction: hybrid={entry.hybrid_score:.0f}, llm={entry.llm_score:.0f}",
    )


def detect_volume_breakout(
    bars: list[Bar],
    volume_ratio: float,
    min_volume_ratio: float = 3.0,
) -> StrategyResult:
    """VOLUME_BREAKOUT: 거래량 돌파 + 저항 돌파."""
    if len(bars) < 20:
        return StrategyResult(False)

    if volume_ratio < min_volume_ratio:
        return StrategyResult(False)

    # 최근 20개 바의 고점 돌파
    recent_high = max(b.high for b in bars[-20:-1])
    current = bars[-1].close
    if current <= recent_high:
        return StrategyResult(False)

    return StrategyResult(
        True,
        SignalType.VOLUME_BREAKOUT,
        f"Volume breakout: ratio={volume_ratio:.1f}x, new high",
    )


def detect_strategies(
    bars: list[Bar],
    regime: MarketRegime,
    entry: WatchlistEntry,
    current_price: float,
    rsi: float | None,
    volume_ratio: float,
    vwap: float,
    config: ScannerConfig,
) -> StrategyResult | None:
    """전략 순차 감지 — 첫 번째 매칭 반환.

    우선순위:
    1. Conviction Entry (Risk Gate 우회)
    2. Bull-only: Golden Cross, Momentum Continuation
    3. General: Momentum, Dip Buy
    4. Counter-trend: RSI Rebound (Bear only)
    """
    # 1. Conviction Entry (always first, bypasses risk gates)
    conv = detect_conviction_entry(
        bars, entry, current_price, vwap, rsi, regime, config
    )
    if conv.detected:
        return conv

    # 2. Bull-market strategies
    if regime in (MarketRegime.BULL, MarketRegime.STRONG_BULL):
        gc = detect_golden_cross(
            bars,
            volume_ratio=volume_ratio,
        )
        if gc.detected:
            return gc

        mc = detect_momentum_continuation(
            bars, regime, llm_score=entry.llm_score
        )
        if mc.detected:
            return mc

    # 3. General strategies
    mom = detect_momentum(bars, max_gain_pct=config.momentum_max_gain_pct)
    if mom.detected:
        return mom

    dip = detect_dip_buy(entry.stock_code, bars, entry, regime)
    if dip.detected:
        return dip

    # 4. Counter-trend (Bear/Sideways only)
    rsi_reb = detect_rsi_rebound(bars, regime)
    if rsi_reb.detected:
        return rsi_reb

    # 5. Volume breakout
    vb = detect_volume_breakout(bars, volume_ratio)
    if vb.detected:
        return vb

    return None
