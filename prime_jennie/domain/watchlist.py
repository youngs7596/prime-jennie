"""워치리스트 모델."""

from datetime import datetime

from pydantic import BaseModel, Field

from .enums import MarketRegime, RiskTag, SectorGroup, TradeTier
from .types import Score, StockCode


class WatchlistEntry(BaseModel):
    """Hot Watchlist 개별 종목."""

    stock_code: StockCode
    stock_name: str
    quant_score: Score = 0.0
    llm_score: Score
    hybrid_score: Score
    rank: int = Field(ge=1, le=50)
    is_tradable: bool
    trade_tier: TradeTier
    risk_tag: RiskTag = RiskTag.NEUTRAL
    veto_applied: bool = False
    sector_group: SectorGroup | None = None
    market_flow: dict | None = None  # 수급 요약
    scored_at: datetime | None = None


class HotWatchlist(BaseModel):
    """Hot Watchlist 전체 (Redis 저장 단위)."""

    generated_at: datetime
    market_regime: MarketRegime
    stocks: list[WatchlistEntry]
    version: str  # "v{timestamp}"

    @property
    def stock_codes(self) -> list[str]:
        return [s.stock_code for s in self.stocks]

    @property
    def tradable_stocks(self) -> list[WatchlistEntry]:
        return [s for s in self.stocks if s.is_tradable]

    def get_stock(self, code: str) -> WatchlistEntry | None:
        return next((s for s in self.stocks if s.stock_code == code), None)
