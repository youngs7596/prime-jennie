"""prime-jennie 도메인 모델 — 서비스 간 데이터 계약의 Single Source of Truth.

Usage:
    from prime_jennie.domain import StockMaster, BuySignal, MarketRegime
    from prime_jennie.domain.config import AppConfig
"""

# --- Types ---
from .types import Multiplier, Percent, PositiveAmount, Quantity, Score, StockCode

# --- Enums ---
from .enums import (
    MOMENTUM_STRATEGIES,
    MarketRegime,
    OrderType,
    RiskTag,
    SectorGroup,
    SectorTier,
    SellReason,
    Sentiment,
    SignalType,
    TradeType,
    TradeTier,
    VixRegime,
)

# --- Stock ---
from .stock import DailyPrice, StockMaster, StockSnapshot

# --- Scoring ---
from .scoring import HybridScore, LLMAnalysis, QuantScore

# --- Watchlist ---
from .watchlist import HotWatchlist, WatchlistEntry

# --- Trading ---
from .trading import (
    BuySignal,
    OrderRequest,
    OrderResult,
    PositionSizingRequest,
    PositionSizingResult,
    SellOrder,
    TradeRecord,
)

# --- Portfolio ---
from .portfolio import DailySnapshot, PortfolioState, Position

# --- Macro ---
from .macro import (
    GlobalSnapshot,
    KeyTheme,
    MacroInsight,
    RiskFactor,
    SectorSignal,
    TradingContext,
)

# --- Sector ---
from .sector import SectorAnalysis, SectorBudget, SectorBudgetEntry

# --- News ---
from .news import NewsArticle, NewsSentiment

# --- Health ---
from .health import DependencyHealth, HealthStatus

__all__ = [
    # Types
    "StockCode",
    "Score",
    "Quantity",
    "PositiveAmount",
    "Multiplier",
    "Percent",
    # Enums
    "MarketRegime",
    "TradeTier",
    "RiskTag",
    "SignalType",
    "SellReason",
    "SectorTier",
    "SectorGroup",
    "OrderType",
    "Sentiment",
    "VixRegime",
    "TradeType",
    "MOMENTUM_STRATEGIES",
    # Stock
    "StockMaster",
    "StockSnapshot",
    "DailyPrice",
    # Scoring
    "QuantScore",
    "LLMAnalysis",
    "HybridScore",
    # Watchlist
    "WatchlistEntry",
    "HotWatchlist",
    # Trading
    "BuySignal",
    "SellOrder",
    "OrderRequest",
    "OrderResult",
    "TradeRecord",
    "PositionSizingRequest",
    "PositionSizingResult",
    # Portfolio
    "Position",
    "PortfolioState",
    "DailySnapshot",
    # Macro
    "SectorSignal",
    "KeyTheme",
    "RiskFactor",
    "MacroInsight",
    "TradingContext",
    "GlobalSnapshot",
    # Sector
    "SectorAnalysis",
    "SectorBudgetEntry",
    "SectorBudget",
    # News
    "NewsArticle",
    "NewsSentiment",
    # Health
    "DependencyHealth",
    "HealthStatus",
]
