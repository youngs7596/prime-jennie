"""Scout Phase 3: Quant Scorer v2 — 잠재력 기반 스코어링.

6개 서브팩터 (100점 만점):
  - 모멘텀 (0-20): RSI, MACD, 가격 모멘텀, 눌림목 감지
  - 품질 (0-20): ROE 트렌드, 재무 건전성
  - 가치 (0-20): PER 할인, PBR 평가
  - 기술 (0-10): 이평선, 거래량 패턴
  - 뉴스 (0-10): 감성 모멘텀
  - 수급 (0-20): 외인/기관 매수 추세, 외인 비율 추세

핵심 전환: "현재 수준" → "변화/개선" (v1 대비).
"""

import logging
import math
from typing import Optional

from prime_jennie.domain.scoring import QuantScore
from prime_jennie.domain.stock import DailyPrice

from .enrichment import EnrichedCandidate

logger = logging.getLogger(__name__)

# ─── v2 가중치 ───────────────────────────────────────────────────

V2_WEIGHTS = {
    "momentum": 20,
    "quality": 20,
    "value": 20,
    "technical": 10,
    "news": 10,
    "supply_demand": 20,
}

# ─── v2 기본값 (데이터 없을 때) ──────────────────────────────────

V2_NEUTRAL = {
    "momentum": 10.0,
    "quality": 10.0,
    "value": 10.0,
    "technical": 5.0,
    "news": 5.0,
    "supply_demand": 10.0,
}


def score_candidate(
    candidate: EnrichedCandidate,
    benchmark_prices: list[DailyPrice] | None = None,
) -> QuantScore:
    """Phase 3: v2 잠재력 기반 스코어링.

    Args:
        candidate: 보강된 후보 종목
        benchmark_prices: KOSPI 벤치마크 일봉 (상대 모멘텀 계산용)

    Returns:
        QuantScore with 6 subscores
    """
    prices = candidate.daily_prices

    # 데이터 부족 시 중립 점수 반환
    if len(prices) < 20:
        return _neutral_score(candidate, reason=f"Insufficient data: {len(prices)} days")

    momentum = _momentum_score(prices, benchmark_prices)
    quality = _quality_score(candidate)
    value = _value_score(candidate)
    technical = _technical_score(prices)
    news = _news_score(candidate)
    supply_demand = _supply_demand_score(candidate)

    total = momentum + quality + value + technical + news + supply_demand
    total = max(0.0, min(100.0, total))

    return QuantScore(
        stock_code=candidate.master.stock_code,
        stock_name=candidate.master.stock_name,
        total_score=round(total, 1),
        momentum_score=round(momentum, 1),
        quality_score=round(quality, 1),
        value_score=round(value, 1),
        technical_score=round(technical, 1),
        news_score=round(news, 1),
        supply_demand_score=round(supply_demand, 1),
    )


# ─── Sub-factor Scoring ─────────────────────────────────────────


def _momentum_score(prices: list[DailyPrice], benchmark: list[DailyPrice] | None) -> float:
    """모멘텀 점수 (0-20): RSI + 가격 모멘텀 + 눌림목."""
    if len(prices) < 20:
        return V2_NEUTRAL["momentum"]

    score = 0.0
    closes = [p.close_price for p in prices]

    # 1. RSI 기반 (0-5): 30-70 범위가 최적, 극단은 감점
    rsi = _compute_rsi(closes, period=14)
    if rsi is not None:
        if 40 <= rsi <= 60:
            score += 5.0
        elif 30 <= rsi < 40 or 60 < rsi <= 70:
            score += 3.5
        elif rsi < 30:
            score += 4.0  # 과매도 = 반등 잠재력
        else:
            score += 1.0  # 과매수

    # 2. 6개월 상대 모멘텀 (0-5)
    if len(closes) >= 120:
        mom_6m = (closes[-1] / closes[-120] - 1) * 100
        score += _linear_map(mom_6m, -20, 30, 0, 5)
    elif len(closes) >= 60:
        mom_3m = (closes[-1] / closes[-60] - 1) * 100
        score += _linear_map(mom_3m, -15, 20, 0, 5)

    # 3. 1개월 단기 모멘텀 (0-5)
    if len(closes) >= 20:
        mom_1m = (closes[-1] / closes[-20] - 1) * 100
        score += _linear_map(mom_1m, -10, 15, 0, 5)

    # 4. 눌림목 감지 (0-5): 6M↑ + 1M↓ = 매수 기회
    if len(closes) >= 120:
        mom_6m = (closes[-1] / closes[-120] - 1) * 100
        mom_1m = (closes[-1] / closes[-20] - 1) * 100
        if mom_6m > 5 and mom_1m < -3:
            score += 5.0  # 눌림목 보너스
        elif mom_6m > 0 and mom_1m < 0:
            score += 2.5

    return min(20.0, score)


def _quality_score(candidate: EnrichedCandidate) -> float:
    """품질 점수 (0-20): ROE + 재무 건전성."""
    ft = candidate.financial_trend
    if not ft:
        return V2_NEUTRAL["quality"]

    score = 0.0

    # ROE (0-10)
    if ft.roe is not None:
        if ft.roe >= 15:
            score += 10.0
        elif ft.roe >= 10:
            score += 8.0
        elif ft.roe >= 5:
            score += 5.0
        elif ft.roe >= 0:
            score += 2.0
        else:
            score += 0.0  # 적자

    # PBR 기반 자산 품질 (0-5)
    if ft.pbr is not None and ft.pbr > 0:
        if ft.pbr < 1.0:
            score += 5.0  # 자산가치 대비 저평가
        elif ft.pbr < 2.0:
            score += 3.0
        elif ft.pbr < 4.0:
            score += 1.5
        else:
            score += 0.5

    # PER 안정성 (0-5): 적정 PER 보유 여부
    if ft.per is not None and ft.per > 0:
        if 5 <= ft.per <= 15:
            score += 5.0
        elif 3 <= ft.per <= 25:
            score += 3.0
        elif ft.per <= 50:
            score += 1.0

    return min(20.0, score)


def _value_score(candidate: EnrichedCandidate) -> float:
    """가치 점수 (0-20): PER 할인 + PBR 평가."""
    ft = candidate.financial_trend
    snap = candidate.snapshot
    if not ft:
        return V2_NEUTRAL["value"]

    score = 0.0

    # PER 할인 (0-10): 업종 평균 대비 저평가
    if ft.per is not None and ft.per > 0:
        if ft.per < 8:
            score += 10.0
        elif ft.per < 12:
            score += 7.0
        elif ft.per < 18:
            score += 4.0
        elif ft.per < 30:
            score += 2.0
        else:
            score += 0.5

    # PBR 평가 (0-5)
    if ft.pbr is not None and ft.pbr > 0:
        if ft.pbr < 0.7:
            score += 5.0
        elif ft.pbr < 1.0:
            score += 4.0
        elif ft.pbr < 1.5:
            score += 2.5
        elif ft.pbr < 3.0:
            score += 1.0

    # 52주 고점 대비 할인 (0-5): 스냅샷 데이터 활용
    if snap and snap.high_52w and snap.price:
        drawdown = (snap.price / snap.high_52w - 1) * 100
        if drawdown < -30:
            score += 2.0  # 너무 떨어지면 감점 (추세 하락)
        elif drawdown < -15:
            score += 5.0  # 적절한 할인
        elif drawdown < -5:
            score += 3.5
        else:
            score += 1.5  # 고점 근접

    return min(20.0, score)


def _technical_score(prices: list[DailyPrice]) -> float:
    """기술 점수 (0-10): 이평선 + 거래량 패턴."""
    if len(prices) < 20:
        return V2_NEUTRAL["technical"]

    score = 0.0
    closes = [p.close_price for p in prices]
    volumes = [p.volume for p in prices]

    # 이평선 정배열 (0-5): 5MA > 20MA > 60MA
    ma5 = sum(closes[-5:]) / 5
    ma20 = sum(closes[-20:]) / 20
    current = closes[-1]

    if current > ma5 > ma20:
        score += 5.0  # 정배열 + 가격 위
    elif current > ma20:
        score += 3.0  # MA20 위
    elif current > ma5:
        score += 1.5

    # 거래량 증가 추세 (0-5): 최근 5일 vs 20일 평균
    if len(volumes) >= 20:
        avg_vol_20 = sum(volumes[-20:]) / 20
        avg_vol_5 = sum(volumes[-5:]) / 5
        if avg_vol_20 > 0:
            vol_ratio = avg_vol_5 / avg_vol_20
            if vol_ratio > 2.0:
                score += 5.0
            elif vol_ratio > 1.5:
                score += 3.5
            elif vol_ratio > 1.0:
                score += 2.0
            else:
                score += 0.5

    return min(10.0, score)


def _news_score(candidate: EnrichedCandidate) -> float:
    """뉴스 점수 (0-10): 감성 모멘텀."""
    sentiment = candidate.news_sentiment_avg
    if sentiment is None:
        return V2_NEUTRAL["news"]

    # sentiment_score: 0=극부정, 50=중립, 100=극긍정
    return _linear_map(sentiment, 20, 80, 0, 10)


def _supply_demand_score(candidate: EnrichedCandidate) -> float:
    """수급 점수 (0-20): 외인/기관 매수 + 외인 비율 추세."""
    it = candidate.investor_trading
    if not it:
        return V2_NEUTRAL["supply_demand"]

    score = 0.0

    # 외인 순매수 (0-8)
    if it.foreign_net_buy_sum > 0:
        score += min(8.0, 4.0 + it.foreign_net_buy_sum / 5e9 * 4.0)
    elif it.foreign_net_buy_sum < 0:
        score += max(0.0, 4.0 + it.foreign_net_buy_sum / 5e9 * 4.0)
    else:
        score += 4.0  # 중립

    # 기관 순매수 (0-6)
    if it.institution_net_buy_sum > 0:
        score += min(6.0, 3.0 + it.institution_net_buy_sum / 5e9 * 3.0)
    elif it.institution_net_buy_sum < 0:
        score += max(0.0, 3.0 + it.institution_net_buy_sum / 5e9 * 3.0)
    else:
        score += 3.0

    # 외인 보유비율 추세 (0-6)
    if it.foreign_ratio_trend is not None:
        if it.foreign_ratio_trend > 1.0:
            score += 6.0
        elif it.foreign_ratio_trend > 0.3:
            score += 4.5
        elif it.foreign_ratio_trend > 0:
            score += 3.0
        elif it.foreign_ratio_trend > -0.5:
            score += 1.5
        else:
            score += 0.0
    else:
        score += 3.0  # 중립

    return min(20.0, score)


# ─── Helpers ─────────────────────────────────────────────────────


def _compute_rsi(closes: list[int | float], period: int = 14) -> Optional[float]:
    """RSI 계산."""
    if len(closes) < period + 1:
        return None

    gains = []
    losses = []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(0, delta))
        losses.append(max(0, -delta))

    # 초기 평균
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # EMA 방식
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _linear_map(value: float, in_min: float, in_max: float, out_min: float, out_max: float) -> float:
    """선형 매핑: value를 [in_min, in_max] → [out_min, out_max]로 변환."""
    clamped = max(in_min, min(in_max, value))
    if in_max == in_min:
        return (out_min + out_max) / 2
    ratio = (clamped - in_min) / (in_max - in_min)
    return out_min + ratio * (out_max - out_min)


def _neutral_score(candidate: EnrichedCandidate, reason: str = "") -> QuantScore:
    """데이터 부족 시 중립 점수."""
    return QuantScore(
        stock_code=candidate.master.stock_code,
        stock_name=candidate.master.stock_name,
        total_score=50.0,
        momentum_score=V2_NEUTRAL["momentum"],
        quality_score=V2_NEUTRAL["quality"],
        value_score=V2_NEUTRAL["value"],
        technical_score=V2_NEUTRAL["technical"],
        news_score=V2_NEUTRAL["news"],
        supply_demand_score=V2_NEUTRAL["supply_demand"],
        is_valid=False,
        invalid_reason=reason,
    )
