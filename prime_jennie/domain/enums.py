"""열거형 정의 — 시스템 전체에서 사용하는 상수값."""

from enum import StrEnum


class MarketRegime(StrEnum):
    """시장 국면"""

    STRONG_BULL = "STRONG_BULL"
    BULL = "BULL"
    SIDEWAYS = "SIDEWAYS"
    BEAR = "BEAR"
    STRONG_BEAR = "STRONG_BEAR"


class TradeTier(StrEnum):
    """거래 등급"""

    TIER1 = "TIER1"  # 최상위 — 비중 100%
    TIER2 = "TIER2"  # 2순위 — 비중 50%
    BLOCKED = "BLOCKED"  # 매수 차단 (Veto Power)


class RiskTag(StrEnum):
    """리스크 태그 (코드 기반 분류)"""

    BULLISH = "BULLISH"
    NEUTRAL = "NEUTRAL"
    CAUTION = "CAUTION"
    DISTRIBUTION_RISK = "DISTRIBUTION_RISK"


class SignalType(StrEnum):
    """매수 시그널 종류"""

    GOLDEN_CROSS = "GOLDEN_CROSS"
    RSI_REBOUND = "RSI_REBOUND"
    MOMENTUM = "MOMENTUM"
    MOMENTUM_CONTINUATION = "MOMENTUM_CONTINUATION"
    DIP_BUY = "DIP_BUY"
    VOLUME_BREAKOUT = "VOLUME_BREAKOUT"
    WATCHLIST_CONVICTION = "WATCHLIST_CONVICTION"
    ORB_BREAKOUT = "ORB_BREAKOUT"


class SellReason(StrEnum):
    """매도 사유"""

    PROFIT_TARGET = "PROFIT_TARGET"
    STOP_LOSS = "STOP_LOSS"
    TRAILING_STOP = "TRAILING_STOP"
    BREAKEVEN_STOP = "BREAKEVEN_STOP"
    RSI_OVERBOUGHT = "RSI_OVERBOUGHT"
    TIME_EXIT = "TIME_EXIT"
    PROFIT_FLOOR = "PROFIT_FLOOR"
    DEATH_CROSS = "DEATH_CROSS"
    RISK_OFF = "RISK_OFF"
    MANUAL = "MANUAL"


class SectorTier(StrEnum):
    """섹터 예산 티어"""

    HOT = "HOT"  # cap=5, 상위 25% & 양수 모멘텀
    WARM = "WARM"  # cap=3, 기본
    COOL = "COOL"  # cap=2, 하위 25% or FALLING_KNIFE


class SectorGroup(StrEnum):
    """15개 대분류 (네이버 79개 세분류 → 15개 그룹)"""

    SEMICONDUCTOR_IT = "반도체/IT"
    BIO_HEALTH = "바이오/헬스케어"
    SECONDARY_BATTERY = "2차전지/소재"
    FINANCE = "금융"
    AUTOMOBILE = "자동차"
    CONSTRUCTION = "건설/부동산"
    CHEMICAL = "화학/에너지"
    STEEL_MATERIAL = "철강/소재"
    FOOD_CONSUMER = "음식료/생활"
    MEDIA_ENTERTAINMENT = "미디어/엔터"
    LOGISTICS_TRANSPORT = "운송/물류"
    TELECOM = "통신"
    UTILITY = "유틸리티"
    DEFENSE_SHIPBUILDING = "조선/방산"
    ETC = "기타"


class OrderType(StrEnum):
    """주문 유형"""

    MARKET = "market"  # 시장가
    LIMIT = "limit"  # 지정가


class Sentiment(StrEnum):
    """매크로 감성"""

    BULLISH = "bullish"
    NEUTRAL_TO_BULLISH = "neutral_to_bullish"
    NEUTRAL = "neutral"
    NEUTRAL_TO_BEARISH = "neutral_to_bearish"
    BEARISH = "bearish"


class VixRegime(StrEnum):
    """VIX 변동성 국면"""

    LOW = "low_vol"
    NORMAL = "normal"
    ELEVATED = "elevated"
    CRISIS = "crisis"


class TradeType(StrEnum):
    """거래 유형"""

    BUY = "BUY"
    SELL = "SELL"


# 모멘텀 전략 (지정가 주문 사용)
MOMENTUM_STRATEGIES = frozenset(
    {
        SignalType.MOMENTUM,
        SignalType.MOMENTUM_CONTINUATION,
    }
)
