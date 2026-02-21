"""일봉 적응 전략 — Production 1분봉 전략을 Daily OHLCV로 변환.

7개 전략:
  1. GOLDEN_CROSS    — MA5/MA20 crossover + vol ratio >= 1.5x
  2. RSI_REBOUND     — RSI threshold crossover (30/35/40 by regime)
  3. MOMENTUM        — 5일 수익률 1.5~7%
  4. MOMENTUM_CONTINUATION — MA5>MA20 + 5일 2~5% + LLM>=65, Bull only
  5. DIP_BUY         — 5일 고점 대비 하락, 워치리스트 D+1~5
  6. VOLUME_BREAKOUT — vol ratio >= 3x + 20일 high breakout
  7. CONVICTION      — hybrid>=70 or llm>=72, D+0~2, RSI<65
"""

from __future__ import annotations

from datetime import date

from prime_jennie.domain.enums import MarketRegime, SignalType
from prime_jennie.services.buyer.position_sizing import calculate_rsi
from prime_jennie.services.monitor.indicators import calculate_sma

from .models import DailyOHLCV, PriceCache, WatchlistEntry


def detect_strategies(
    entry: WatchlistEntry,
    price_cache: PriceCache,
    current_date: date,
    regime: MarketRegime,
) -> list[SignalType]:
    """워치리스트 항목에 대해 해당 날짜 기준 발동 가능한 전략 리스트 반환.

    우선순위: CONVICTION > GOLDEN_CROSS > VOLUME_BREAKOUT > RSI_REBOUND
              > MOMENTUM > MOMENTUM_CONTINUATION > DIP_BUY
    """
    history = price_cache.get_history_until(entry.stock_code, current_date, n=60)
    if len(history) < 21:
        return []

    close_prices = [p.close_price for p in history]
    volumes = [p.volume for p in history]

    signals: list[SignalType] = []

    # --- CONVICTION ---
    if _check_conviction(entry, current_date, close_prices, regime):
        signals.append(SignalType.WATCHLIST_CONVICTION)

    # --- GOLDEN_CROSS ---
    if _check_golden_cross(close_prices, volumes):
        signals.append(SignalType.GOLDEN_CROSS)

    # --- VOLUME_BREAKOUT ---
    if _check_volume_breakout(history, close_prices, volumes):
        signals.append(SignalType.VOLUME_BREAKOUT)

    # --- RSI_REBOUND ---
    if _check_rsi_rebound(close_prices, regime):
        signals.append(SignalType.RSI_REBOUND)

    # --- MOMENTUM ---
    if _check_momentum(close_prices):
        signals.append(SignalType.MOMENTUM)

    # --- MOMENTUM_CONTINUATION ---
    if _check_momentum_continuation(close_prices, entry, regime):
        signals.append(SignalType.MOMENTUM_CONTINUATION)

    # --- DIP_BUY ---
    if _check_dip_buy(entry, current_date, close_prices):
        signals.append(SignalType.DIP_BUY)

    return signals


# --- Individual Strategy Checks ---


def _check_golden_cross(close_prices: list[float], volumes: list[int]) -> bool:
    """MA5/MA20 crossover (close) + vol ratio >= 1.5x."""
    if len(close_prices) < 21:
        return False

    sma5 = calculate_sma(close_prices, 5)
    sma20 = calculate_sma(close_prices, 20)

    curr_s = sma5[-1]
    curr_l = sma20[-1]
    prev_s = sma5[-2]
    prev_l = sma20[-2]

    if curr_s is None or curr_l is None or prev_s is None or prev_l is None:
        return False

    # 상향 돌파: 이전 short <= long, 현재 short > long
    crossover = prev_s <= prev_l and curr_s > curr_l

    if not crossover:
        return False

    # 거래량 조건: 최근 vol / 20일 평균 >= 1.5
    if len(volumes) < 20:
        return False
    avg_vol = sum(volumes[-20:]) / 20
    if avg_vol <= 0:
        return False
    vol_ratio = volumes[-1] / avg_vol
    return vol_ratio >= 1.5


def _check_rsi_rebound(close_prices: list[float], regime: MarketRegime) -> bool:
    """RSI threshold crossover (국면별 임계값)."""
    if len(close_prices) < 16:
        return False

    rsi_now = calculate_rsi(close_prices, period=14)
    # 하루 전까지의 RSI
    rsi_prev = calculate_rsi(close_prices[:-1], period=14)

    if rsi_now is None or rsi_prev is None:
        return False

    # 국면별 RSI 반등 기준
    threshold = {
        MarketRegime.STRONG_BULL: 30.0,
        MarketRegime.BULL: 35.0,
        MarketRegime.SIDEWAYS: 35.0,
        MarketRegime.BEAR: 40.0,
        MarketRegime.STRONG_BEAR: 40.0,
    }.get(regime, 35.0)

    # 이전 RSI가 threshold 이하 → 현재 RSI가 threshold 초과
    return rsi_prev <= threshold and rsi_now > threshold


def _check_momentum(close_prices: list[float]) -> bool:
    """5일 수익률 1.5~7%."""
    if len(close_prices) < 6:
        return False
    ret_5d = (close_prices[-1] - close_prices[-6]) / close_prices[-6] * 100
    return 1.5 <= ret_5d <= 7.0


def _check_momentum_continuation(
    close_prices: list[float],
    entry: WatchlistEntry,
    regime: MarketRegime,
) -> bool:
    """MA5>MA20 + 5일 2~5% + LLM>=65, Bull only."""
    if regime not in (MarketRegime.STRONG_BULL, MarketRegime.BULL):
        return False

    if entry.llm_score < 65:
        return False

    if len(close_prices) < 21:
        return False

    sma5 = calculate_sma(close_prices, 5)
    sma20 = calculate_sma(close_prices, 20)

    if sma5[-1] is None or sma20[-1] is None:
        return False
    if sma5[-1] <= sma20[-1]:
        return False

    if len(close_prices) < 6:
        return False
    ret_5d = (close_prices[-1] - close_prices[-6]) / close_prices[-6] * 100
    return 2.0 <= ret_5d <= 5.0


def _check_dip_buy(
    entry: WatchlistEntry,
    current_date: date,
    close_prices: list[float],
) -> bool:
    """5일 고점 대비 하락, 워치리스트 D+1~5."""
    # D+1~5 체크 (워치리스트 등재일 기준)
    days_since = (current_date - entry.snapshot_date).days
    if days_since < 1 or days_since > 5:
        return False

    if len(close_prices) < 6:
        return False

    high_5d = max(close_prices[-6:-1])  # 이전 5일 고점 (오늘 제외)
    if high_5d <= 0:
        return False
    dip_pct = (close_prices[-1] - high_5d) / high_5d * 100
    # 3~8% 하락
    return -8.0 <= dip_pct <= -3.0


def _check_volume_breakout(
    history: list[DailyOHLCV],
    close_prices: list[float],
    volumes: list[int],
) -> bool:
    """vol ratio >= 3x + 20일 high breakout."""
    if len(history) < 21:
        return False

    # 거래량 조건
    avg_vol = sum(volumes[-20:]) / 20
    if avg_vol <= 0:
        return False
    vol_ratio = volumes[-1] / avg_vol
    if vol_ratio < 3.0:
        return False

    # 20일 고가 돌파 (오늘 제외한 이전 20일)
    high_20d = max(p.high_price for p in history[-21:-1])
    return close_prices[-1] > high_20d


def _check_conviction(
    entry: WatchlistEntry,
    current_date: date,
    close_prices: list[float],
    regime: MarketRegime,
) -> bool:
    """hybrid>=70 or llm>=72, D+0~2, RSI<65."""
    days_since = (current_date - entry.snapshot_date).days
    if days_since < 0 or days_since > 2:
        return False

    if entry.hybrid_score < 70 and entry.llm_score < 72:
        return False

    if len(close_prices) >= 15:
        rsi = calculate_rsi(close_prices, period=14)
        if rsi is not None and rsi >= 65:
            return False

    return True
