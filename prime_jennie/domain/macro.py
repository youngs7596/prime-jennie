"""매크로 인사이트 및 트레이딩 컨텍스트 모델."""

from datetime import date, datetime

from pydantic import BaseModel, Field

from .enums import MarketRegime, SectorGroup, Sentiment, VixRegime
from .types import Multiplier, Score


class SectorSignal(BaseModel):
    """섹터별 매크로 신호."""

    sector_group: SectorGroup
    signal: str  # "HOT" | "NEUTRAL" | "AVOID"
    confidence: str | None = None  # "HIGH" | "MID" | "LOW"
    reasoning: str | None = None


class KeyTheme(BaseModel):
    """핵심 투자 테마."""

    rank: int
    theme: str
    description: str
    impact: str  # "Positive" | "Negative" | "Mixed"
    duration: str | None = None


class RiskFactor(BaseModel):
    """리스크 요인."""

    name: str
    severity: str  # "HIGH" | "MID" | "LOW"
    duration_days: int | None = None


class MacroInsight(BaseModel):
    """일일 매크로 인사이트 (Council 출력)."""

    insight_date: date
    sentiment: Sentiment
    sentiment_score: Score
    regime_hint: str = Field(max_length=200)
    sector_signals: list[SectorSignal] = []
    key_themes: list[KeyTheme] = []
    risk_factors: list[RiskFactor] = []
    sectors_to_favor: list[SectorGroup] = []
    sectors_to_avoid: list[SectorGroup] = []
    position_size_pct: int = Field(ge=50, le=130, default=100)
    stop_loss_adjust_pct: int = Field(ge=80, le=150, default=100)
    political_risk_level: str = "low"  # low | medium | high | critical
    council_cost_usd: float | None = None
    # 글로벌 스냅샷 요약
    vix_value: float | None = None
    vix_regime: VixRegime = VixRegime.NORMAL
    usd_krw: float | None = None
    kospi_index: float | None = None
    kosdaq_index: float | None = None


class TradingContext(BaseModel):
    """트레이딩 컨텍스트 (서비스 소비용)."""

    date: date
    market_regime: MarketRegime
    position_multiplier: Multiplier = 1.0
    stop_loss_multiplier: Multiplier = 1.0
    vix_regime: VixRegime = VixRegime.NORMAL
    risk_off_level: int = Field(ge=0, le=10, default=0)
    favor_sectors: list[SectorGroup] = []
    avoid_sectors: list[SectorGroup] = []
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
    fed_rate: float | None = None
    treasury_10y: float | None = None
    us_cpi_yoy: float | None = None
    vix: float | None = None
    vix_regime: VixRegime = VixRegime.NORMAL
    # FX
    dxy_index: float | None = None
    usd_krw: float | None = None
    # Korea
    bok_rate: float | None = None
    kospi_index: float | None = None
    kospi_change_pct: float | None = None
    kosdaq_index: float | None = None
    kosdaq_change_pct: float | None = None
    # Investor flow
    kospi_foreign_net: float | None = None  # 억원
    kosdaq_foreign_net: float | None = None
    kospi_institutional_net: float | None = None
    kospi_retail_net: float | None = None
    # Metadata
    completeness_pct: float = 0.0
    data_sources: list[str] = []
