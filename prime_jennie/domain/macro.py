"""매크로 인사이트 및 트레이딩 컨텍스트 모델."""

from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from .enums import MarketRegime, SectorGroup, VixRegime, Sentiment
from .types import Multiplier, Score


class SectorSignal(BaseModel):
    """섹터별 매크로 신호."""

    sector_group: SectorGroup
    signal: str  # "HOT" | "NEUTRAL" | "AVOID"
    confidence: Optional[str] = None  # "HIGH" | "MID" | "LOW"
    reasoning: Optional[str] = None


class KeyTheme(BaseModel):
    """핵심 투자 테마."""

    rank: int
    theme: str
    description: str
    impact: str  # "Positive" | "Negative" | "Mixed"
    duration: Optional[str] = None


class RiskFactor(BaseModel):
    """리스크 요인."""

    name: str
    severity: str  # "HIGH" | "MID" | "LOW"
    duration_days: Optional[int] = None


class MacroInsight(BaseModel):
    """일일 매크로 인사이트 (Council 출력)."""

    insight_date: date
    sentiment: Sentiment
    sentiment_score: Score
    regime_hint: str = Field(max_length=200)
    sector_signals: List[SectorSignal] = []
    key_themes: List[KeyTheme] = []
    risk_factors: List[RiskFactor] = []
    sectors_to_favor: List[SectorGroup] = []
    sectors_to_avoid: List[SectorGroup] = []
    position_size_pct: int = Field(ge=50, le=130, default=100)
    stop_loss_adjust_pct: int = Field(ge=80, le=150, default=100)
    political_risk_level: str = "low"  # low | medium | high | critical
    council_cost_usd: Optional[float] = None
    # 글로벌 스냅샷 요약
    vix_value: Optional[float] = None
    vix_regime: VixRegime = VixRegime.NORMAL
    usd_krw: Optional[float] = None
    kospi_index: Optional[float] = None
    kosdaq_index: Optional[float] = None


class TradingContext(BaseModel):
    """트레이딩 컨텍스트 (서비스 소비용)."""

    date: date
    market_regime: MarketRegime
    position_multiplier: Multiplier = 1.0
    stop_loss_multiplier: Multiplier = 1.0
    vix_regime: VixRegime = VixRegime.NORMAL
    risk_off_level: int = Field(ge=0, le=10, default=0)
    favor_sectors: List[SectorGroup] = []
    avoid_sectors: List[SectorGroup] = []
    is_high_volatility: bool = False

    @classmethod
    def default(cls) -> "TradingContext":
        """매크로 데이터 없을 때 안전 기본값."""
        from datetime import date as d

        return cls(
            date=d.today(),
            market_regime=MarketRegime.SIDEWAYS,
            position_multiplier=0.8,
            stop_loss_multiplier=1.2,
        )


class GlobalSnapshot(BaseModel):
    """글로벌 매크로 스냅샷 (수집 데이터)."""

    snapshot_date: date
    timestamp: datetime
    # US
    fed_rate: Optional[float] = None
    treasury_10y: Optional[float] = None
    us_cpi_yoy: Optional[float] = None
    vix: Optional[float] = None
    vix_regime: VixRegime = VixRegime.NORMAL
    # FX
    dxy_index: Optional[float] = None
    usd_krw: Optional[float] = None
    # Korea
    bok_rate: Optional[float] = None
    kospi_index: Optional[float] = None
    kospi_change_pct: Optional[float] = None
    kosdaq_index: Optional[float] = None
    kosdaq_change_pct: Optional[float] = None
    # Investor flow
    kospi_foreign_net: Optional[float] = None  # 억원
    kosdaq_foreign_net: Optional[float] = None
    kospi_institutional_net: Optional[float] = None
    kospi_retail_net: Optional[float] = None
    # Metadata
    completeness_pct: float = 0.0
    data_sources: List[str] = []
