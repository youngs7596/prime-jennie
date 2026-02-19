# 02. Domain Models — prime-jennie

> 서비스 간 데이터 계약을 Pydantic v2 모델로 정의한다.
> 이 파일의 모델이 **Single Source of Truth** — 코드에서 `prime_jennie.domain`으로 import.

## 1. 기본 타입 (Primitives)

```python
# prime_jennie/domain/types.py

from typing import Annotated
from pydantic import Field

# 종목코드: 6자리 숫자 문자열
StockCode = Annotated[str, Field(pattern=r"^\d{6}$", examples=["005930"])]

# 점수: 0~100 범위 실수
Score = Annotated[float, Field(ge=0, le=100)]

# 양수 금액
PositiveAmount = Annotated[float, Field(gt=0)]

# 수량: 양의 정수
Quantity = Annotated[int, Field(gt=0)]

# 비율: 0~1 범위 (퍼센트가 아닌 비율)
Ratio = Annotated[float, Field(ge=0, le=1)]

# 배율: 0.5~2.0 범위
Multiplier = Annotated[float, Field(ge=0.3, le=2.0)]
```

## 2. 열거형 (Enums)

```python
# prime_jennie/domain/enums.py

from enum import StrEnum

class MarketRegime(StrEnum):
    STRONG_BULL = "STRONG_BULL"
    BULL = "BULL"
    SIDEWAYS = "SIDEWAYS"
    BEAR = "BEAR"
    STRONG_BEAR = "STRONG_BEAR"

class TradeTier(StrEnum):
    TIER1 = "TIER1"        # 최상위 (비중 100%)
    TIER2 = "TIER2"        # 2순위 (비중 50%)
    BLOCKED = "BLOCKED"    # 매수 차단 (Veto Power)

class RiskTag(StrEnum):
    BULLISH = "BULLISH"
    NEUTRAL = "NEUTRAL"
    CAUTION = "CAUTION"
    DISTRIBUTION_RISK = "DISTRIBUTION_RISK"  # → BLOCKED

class SignalType(StrEnum):
    GOLDEN_CROSS = "GOLDEN_CROSS"
    RSI_REBOUND = "RSI_REBOUND"
    MOMENTUM = "MOMENTUM"
    MOMENTUM_CONTINUATION = "MOMENTUM_CONTINUATION"
    DIP_BUY = "DIP_BUY"
    VOLUME_BREAKOUT = "VOLUME_BREAKOUT"
    WATCHLIST_CONVICTION = "WATCHLIST_CONVICTION"

class SellReason(StrEnum):
    PROFIT_TARGET = "PROFIT_TARGET"
    STOP_LOSS = "STOP_LOSS"
    TRAILING_STOP = "TRAILING_STOP"
    RSI_OVERBOUGHT = "RSI_OVERBOUGHT"
    TIME_EXIT = "TIME_EXIT"
    RISK_OFF = "RISK_OFF"
    MANUAL = "MANUAL"

class SectorTier(StrEnum):
    HOT = "HOT"      # cap=5, 상위 25% & 양수
    WARM = "WARM"    # cap=3, 기본
    COOL = "COOL"    # cap=2, 하위 25% or FALLING_KNIFE

class SectorGroup(StrEnum):
    """14개 대분류 (네이버 79개 세분류 → 14개 그룹)"""
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
    ETC = "기타"

class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"

class Sentiment(StrEnum):
    BULLISH = "bullish"
    NEUTRAL_TO_BULLISH = "neutral_to_bullish"
    NEUTRAL = "neutral"
    NEUTRAL_TO_BEARISH = "neutral_to_bearish"
    BEARISH = "bearish"

class VixRegime(StrEnum):
    LOW = "low_vol"
    NORMAL = "normal"
    ELEVATED = "elevated"
    CRISIS = "crisis"
```

## 3. 종목 관련 모델

```python
# prime_jennie/domain/stock.py

from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel
from .types import StockCode, Score
from .enums import SectorGroup

class StockMaster(BaseModel):
    """종목 마스터 (STOCK_MASTER 테이블)"""
    stock_code: StockCode
    stock_name: str
    market_cap: int                         # 시가총액 (원)
    sector_naver: str                       # 네이버 세분류 (79개)
    sector_group: SectorGroup               # 대분류 (14개)
    is_active: bool = True

class StockSnapshot(BaseModel):
    """실시간 스냅샷 (KIS API 응답)"""
    stock_code: StockCode
    price: int                              # 현재가
    open_price: int
    high_price: int
    low_price: int
    volume: int                             # 거래량
    change_pct: float                       # 등락률 (%)
    per: Optional[float] = None
    pbr: Optional[float] = None
    market_cap: Optional[int] = None
    high_52w: Optional[int] = None
    low_52w: Optional[int] = None
    timestamp: datetime

class DailyPrice(BaseModel):
    """일별 OHLCV"""
    stock_code: StockCode
    price_date: date
    open_price: int
    high_price: int
    low_price: int
    close_price: int
    volume: int
```

## 4. 스코어링 모델

```python
# prime_jennie/domain/scoring.py

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, field_validator
from .types import StockCode, Score
from .enums import RiskTag, TradeTier

class QuantScore(BaseModel):
    """Quant Scorer v2 출력"""
    stock_code: StockCode
    stock_name: str
    total_score: Score                      # 0-100
    momentum_score: float                   # 0-20
    quality_score: float                    # 0-20
    value_score: float                      # 0-20
    technical_score: float                  # 0-10
    news_score: float                       # 0-10
    supply_demand_score: float              # 0-20
    matched_conditions: List[str] = []
    condition_win_rate: Optional[float] = None
    condition_confidence: Optional[str] = None
    is_valid: bool = True
    invalid_reason: Optional[str] = None

    @field_validator("total_score")
    @classmethod
    def check_total(cls, v, info):
        """서브스코어 합산 검증 (허용 오차 1.0)"""
        data = info.data
        if all(k in data for k in ["momentum_score", "quality_score", "value_score",
                                     "technical_score", "news_score", "supply_demand_score"]):
            expected = sum([
                data["momentum_score"], data["quality_score"], data["value_score"],
                data["technical_score"], data["news_score"], data["supply_demand_score"]
            ])
            if abs(v - expected) > 1.0:
                raise ValueError(f"total_score({v}) != subscores sum({expected})")
        return v

class LLMScore(BaseModel):
    """LLM Analyst 출력 (Unified Analyst Pipeline)"""
    stock_code: StockCode
    raw_score: Score                        # LLM 원시 점수
    clamped_score: Score                    # ±15pt 클램핑 적용
    grade: str                              # S/A/B/C/D
    reason: str                             # 100자 이상
    scored_at: datetime

    @field_validator("reason")
    @classmethod
    def reason_min_length(cls, v):
        if len(v) < 20:
            raise ValueError("reason must be at least 20 chars")
        return v

class HybridScore(BaseModel):
    """최종 평가 결과 (Quant + LLM)"""
    stock_code: StockCode
    stock_name: str
    quant_score: Score
    llm_score: Score
    hybrid_score: Score                     # = llm_score (클램핑 후)
    risk_tag: RiskTag
    trade_tier: TradeTier
    is_tradable: bool
    veto_applied: bool = False
    scored_at: datetime

    @field_validator("is_tradable")
    @classmethod
    def blocked_means_not_tradable(cls, v, info):
        if info.data.get("trade_tier") == TradeTier.BLOCKED and v:
            raise ValueError("BLOCKED tier must have is_tradable=False")
        return v

    @field_validator("veto_applied")
    @classmethod
    def distribution_risk_means_veto(cls, v, info):
        if info.data.get("risk_tag") == RiskTag.DISTRIBUTION_RISK and not v:
            raise ValueError("DISTRIBUTION_RISK must have veto_applied=True")
        return v
```

## 5. 워치리스트 모델

```python
# prime_jennie/domain/watchlist.py

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field
from .types import StockCode, Score
from .enums import MarketRegime, TradeTier, RiskTag, SectorGroup

class WatchlistEntry(BaseModel):
    """Hot Watchlist 개별 종목"""
    stock_code: StockCode
    stock_name: str
    llm_score: Score
    hybrid_score: Score
    rank: int = Field(ge=1, le=50)
    is_tradable: bool
    trade_tier: TradeTier
    risk_tag: RiskTag = RiskTag.NEUTRAL
    veto_applied: bool = False
    sector_group: Optional[SectorGroup] = None
    market_flow: Optional[dict] = None      # 수급 요약
    scored_at: Optional[datetime] = None

class HotWatchlist(BaseModel):
    """Hot Watchlist 전체 (Redis 저장 단위)"""
    generated_at: datetime
    market_regime: MarketRegime
    stocks: List[WatchlistEntry]
    version: str                             # "v{timestamp}"

    @property
    def stock_codes(self) -> List[str]:
        return [s.stock_code for s in self.stocks]

    @property
    def tradable_stocks(self) -> List[WatchlistEntry]:
        return [s for s in self.stocks if s.is_tradable]

    def get_stock(self, code: str) -> Optional[WatchlistEntry]:
        return next((s for s in self.stocks if s.stock_code == code), None)
```

## 6. 트레이딩 시그널 모델

```python
# prime_jennie/domain/trading.py

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field
from .types import StockCode, Score, Quantity, Multiplier
from .enums import (
    SignalType, SellReason, MarketRegime, TradeTier, RiskTag,
    OrderType, SectorGroup
)

class BuySignal(BaseModel):
    """매수 시그널 (Scanner → Executor, Redis Stream 메시지)"""
    stock_code: StockCode
    stock_name: str
    signal_type: SignalType
    signal_price: int                       # 시그널 발생 시점 가격
    llm_score: Score
    hybrid_score: Score
    is_tradable: bool = True
    trade_tier: TradeTier
    risk_tag: RiskTag = RiskTag.NEUTRAL
    market_regime: MarketRegime
    source: str = "scanner"                 # scanner | conviction | manual
    timestamp: datetime
    # 기술 지표 컨텍스트
    rsi_value: Optional[float] = None
    volume_ratio: Optional[float] = None
    vwap: Optional[float] = None
    # 매크로 컨텍스트
    position_multiplier: Multiplier = 1.0

class SellOrder(BaseModel):
    """매도 주문 (Monitor → Executor, Redis Stream 메시지)"""
    stock_code: StockCode
    stock_name: str
    sell_reason: SellReason
    current_price: int
    quantity: Quantity
    timestamp: datetime
    # 수익률 컨텍스트
    buy_price: Optional[int] = None
    profit_pct: Optional[float] = None
    holding_days: Optional[int] = None

class OrderRequest(BaseModel):
    """KIS Gateway 주문 요청"""
    stock_code: StockCode
    quantity: Quantity
    order_type: OrderType = OrderType.MARKET
    price: Optional[int] = None             # limit 주문 시 필수

class OrderResult(BaseModel):
    """KIS Gateway 주문 결과"""
    success: bool
    order_no: Optional[str] = None
    stock_code: StockCode
    quantity: int
    price: int
    message: Optional[str] = None

class TradeRecord(BaseModel):
    """거래 기록 (DB 저장용)"""
    stock_code: StockCode
    stock_name: str
    trade_type: str                         # "BUY" | "SELL"
    quantity: int
    price: int
    total_amount: int
    reason: str
    strategy_signal: Optional[str] = None
    market_regime: Optional[MarketRegime] = None
    llm_score: Optional[Score] = None
    hybrid_score: Optional[Score] = None
    trade_tier: Optional[TradeTier] = None
    timestamp: datetime

class PositionSizingRequest(BaseModel):
    """포지션 사이징 입력"""
    stock_code: StockCode
    stock_price: int
    atr: float                              # Average True Range
    available_cash: int
    portfolio_value: int
    llm_score: Score
    trade_tier: TradeTier
    sector_group: Optional[SectorGroup] = None
    held_sector_groups: list[SectorGroup] = Field(default_factory=list)
    portfolio_risk_pct: float = 0.0
    position_multiplier: Multiplier = 1.0
    stale_days: int = 0

class PositionSizingResult(BaseModel):
    """포지션 사이징 결과"""
    quantity: int
    target_weight_pct: float               # 목표 비중 (%)
    actual_weight_pct: float               # 실제 비중 (%)
    applied_multipliers: dict[str, float]  # 적용된 배율 상세
    reasoning: str
```

## 7. 포트폴리오 모델

```python
# prime_jennie/domain/portfolio.py

from datetime import date, datetime
from typing import List, Optional
from pydantic import BaseModel
from .types import StockCode
from .enums import SectorGroup

class Position(BaseModel):
    """보유 포지션"""
    stock_code: StockCode
    stock_name: str
    quantity: int
    average_buy_price: int
    total_buy_amount: int
    current_price: Optional[int] = None
    current_value: Optional[int] = None
    profit_pct: Optional[float] = None
    sector_group: Optional[SectorGroup] = None
    high_watermark: Optional[int] = None    # 보유 중 최고가
    stop_loss_price: Optional[int] = None
    bought_at: Optional[datetime] = None

class PortfolioState(BaseModel):
    """포트폴리오 전체 상태"""
    positions: List[Position]
    cash_balance: int
    total_asset: int                        # cash + 주식 평가액
    stock_eval_amount: int
    position_count: int
    timestamp: datetime

    @property
    def cash_ratio(self) -> float:
        if self.total_asset == 0:
            return 1.0
        return self.cash_balance / self.total_asset

    @property
    def sector_distribution(self) -> dict[str, int]:
        """섹터별 보유 종목 수"""
        dist: dict[str, int] = {}
        for p in self.positions:
            group = p.sector_group or SectorGroup.ETC
            dist[group] = dist.get(group, 0) + 1
        return dist

class DailySnapshot(BaseModel):
    """일일 자산 스냅샷"""
    snapshot_date: date
    total_asset: int
    cash_balance: int
    stock_eval_amount: int
    total_profit_loss: Optional[int] = None
    realized_profit_loss: Optional[int] = None
```

## 8. 매크로 모델

```python
# prime_jennie/domain/macro.py

from datetime import date, datetime
from typing import List, Optional, Dict
from pydantic import BaseModel, Field
from .types import Score, Multiplier
from .enums import (
    Sentiment, MarketRegime, VixRegime, SectorGroup, SectorTier
)

class SectorSignal(BaseModel):
    """섹터별 매크로 신호"""
    sector_group: SectorGroup
    signal: str                             # "HOT" | "NEUTRAL" | "AVOID"
    confidence: Optional[str] = None        # "HIGH" | "MID" | "LOW"
    reasoning: Optional[str] = None

class KeyTheme(BaseModel):
    """핵심 투자 테마"""
    rank: int
    theme: str
    description: str
    impact: str                             # "Positive" | "Negative" | "Mixed"
    duration: Optional[str] = None

class RiskFactor(BaseModel):
    """리스크 요인"""
    name: str
    severity: str                           # "HIGH" | "MID" | "LOW"
    duration_days: Optional[int] = None

class MacroInsight(BaseModel):
    """일일 매크로 인사이트 (Council 출력)"""
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
    political_risk_level: str = "low"       # low | medium | high | critical
    council_cost_usd: Optional[float] = None
    # 글로벌 스냅샷 요약
    vix_value: Optional[float] = None
    vix_regime: VixRegime = VixRegime.NORMAL
    usd_krw: Optional[float] = None
    kospi_index: Optional[float] = None
    kosdaq_index: Optional[float] = None

class TradingContext(BaseModel):
    """트레이딩 컨텍스트 (서비스 소비용)"""
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
        """매크로 데이터 없을 때 안전 기본값"""
        from datetime import date as d
        return cls(
            date=d.today(),
            market_regime=MarketRegime.SIDEWAYS,
            position_multiplier=0.8,
            stop_loss_multiplier=1.2,
        )

class GlobalSnapshot(BaseModel):
    """글로벌 매크로 스냅샷 (수집 데이터)"""
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
    kospi_foreign_net: Optional[float] = None    # 억원
    kosdaq_foreign_net: Optional[float] = None
    kospi_institutional_net: Optional[float] = None
    kospi_retail_net: Optional[float] = None
    # Metadata
    completeness_pct: float = 0.0
    data_sources: List[str] = []
```

## 9. 섹터 예산 모델

```python
# prime_jennie/domain/sector.py

from typing import Dict, Optional, List
from pydantic import BaseModel, Field
from .enums import SectorGroup, SectorTier

class SectorAnalysis(BaseModel):
    """섹터별 분석 결과"""
    sector_group: SectorGroup
    avg_return_pct: float
    stock_count: int
    is_falling_knife: bool = False

class SectorBudgetEntry(BaseModel):
    """개별 섹터 예산"""
    sector_group: SectorGroup
    tier: SectorTier
    watchlist_cap: int = Field(ge=0, le=10)     # Scout 선정 상한
    portfolio_cap: int = Field(ge=0, le=10)     # 포트폴리오 보유 상한
    effective_cap: int = Field(ge=0, le=10)     # 실효 상한 (보유 감안)
    held_count: int = 0                         # 현재 보유 수

class SectorBudget(BaseModel):
    """전체 섹터 예산 (Redis 저장 단위)"""
    entries: Dict[SectorGroup, SectorBudgetEntry]
    generated_at: str
    council_overrides_applied: bool = False

    def get_cap(self, group: SectorGroup) -> int:
        entry = self.entries.get(group)
        return entry.effective_cap if entry else 3  # 기본 WARM cap

    def is_available(self, group: SectorGroup) -> bool:
        return self.get_cap(group) > 0
```

## 10. 뉴스 모델

```python
# prime_jennie/domain/news.py

from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel, HttpUrl
from .types import StockCode, Score

class NewsArticle(BaseModel):
    """수집된 뉴스 기사"""
    stock_code: StockCode
    stock_name: str
    press: str
    headline: str
    summary: Optional[str] = None
    category: Optional[str] = None          # 실적, 수주, 규제 등
    article_url: str
    published_at: datetime
    source: str                             # NAVER, DAUM, etc.

class NewsSentiment(BaseModel):
    """뉴스 감성 분석 결과"""
    stock_code: StockCode
    news_date: date
    press: str
    headline: str
    summary: Optional[str] = None
    sentiment_score: Score                  # 0=극부정, 50=중립, 100=극긍정
    sentiment_reason: Optional[str] = None
    category: Optional[str] = None
    article_url: str                        # Unique constraint
    published_at: datetime
```

## 11. 설정 모델

```python
# prime_jennie/domain/config.py

from pydantic_settings import BaseSettings
from .enums import MarketRegime

class RiskConfig(BaseSettings):
    """리스크 관리 설정"""
    max_portfolio_size: int = 10
    max_sector_stocks: int = 3
    portfolio_guard_enabled: bool = True
    dynamic_sector_budget_enabled: bool = True
    # 국면별 현금 하한선
    cash_floor_strong_bull_pct: float = 5.0
    cash_floor_bull_pct: float = 10.0
    cash_floor_sideways_pct: float = 15.0
    cash_floor_bear_pct: float = 25.0

    class Config:
        env_prefix = "RISK_"

    def get_cash_floor(self, regime: MarketRegime) -> float:
        return {
            MarketRegime.STRONG_BULL: self.cash_floor_strong_bull_pct,
            MarketRegime.BULL: self.cash_floor_bull_pct,
            MarketRegime.SIDEWAYS: self.cash_floor_sideways_pct,
            MarketRegime.BEAR: self.cash_floor_bear_pct,
            MarketRegime.STRONG_BEAR: self.cash_floor_bear_pct,
        }.get(regime, self.cash_floor_sideways_pct)

class ScoringConfig(BaseSettings):
    """스코어링 설정"""
    quant_scorer_version: str = "v2"
    unified_analyst_enabled: bool = True
    llm_clamp_range: int = 15               # ±15pt 가드레일
    hard_floor_score: float = 40.0          # 이하 → BLOCKED

    class Config:
        env_prefix = "SCORING_"

class ScannerConfig(BaseSettings):
    """Scanner 설정"""
    min_required_bars: int = 20
    signal_cooldown_seconds: int = 600
    rsi_guard_max: float = 75.0
    volume_ratio_warning: float = 2.0
    vwap_deviation_warning: float = 0.02
    no_trade_window_start: str = "09:00"
    no_trade_window_end: str = "09:15"
    danger_zone_start: str = "14:00"
    danger_zone_end: str = "15:00"
    # Conviction Entry
    conviction_entry_enabled: bool = True
    conviction_min_hybrid_score: float = 70.0
    conviction_min_llm_score: float = 72.0
    conviction_max_gain_pct: float = 3.0
    conviction_window_start: str = "09:15"
    conviction_window_end: str = "10:30"
    # Momentum
    momentum_limit_order_enabled: bool = True
    momentum_limit_premium: float = 0.003
    momentum_limit_timeout_sec: int = 10
    momentum_confirmation_bars: int = 1
    momentum_max_gain_pct: float = 7.0

    class Config:
        env_prefix = "SCANNER_"

class LLMConfig(BaseSettings):
    """LLM 설정"""
    tier_fast_provider: str = "ollama"
    tier_reasoning_provider: str = "deepseek_cloud"
    tier_thinking_provider: str = "deepseek_cloud"
    vllm_llm_url: str = "http://localhost:8001/v1"
    vllm_embed_url: str = "http://localhost:8002/v1"
    vllm_max_model_len: int = 4096

    class Config:
        env_prefix = "LLM_"

class ScoutConfig(BaseSettings):
    """Scout 설정"""
    max_watchlist_size: int = 20
    universe_size: int = 200
    enable_news_analysis: bool = True

    class Config:
        env_prefix = "SCOUT_"
```

## 12. 헬스 체크 모델

```python
# prime_jennie/domain/health.py

from datetime import datetime
from typing import Dict, Optional
from pydantic import BaseModel

class DependencyHealth(BaseModel):
    status: str                             # "healthy" | "degraded" | "down"
    latency_ms: Optional[float] = None
    message: Optional[str] = None

class HealthStatus(BaseModel):
    service: str
    status: str                             # "healthy" | "degraded" | "unhealthy"
    uptime_seconds: float
    version: str = "1.0.0"
    dependencies: Dict[str, DependencyHealth] = {}
    timestamp: datetime
```

## 13. 모델 간 관계도

```
StockMaster ──────────────────────────────────────────────┐
    │                                                      │
    ▼                                                      │
QuantScore ─────► HybridScore ─────► WatchlistEntry       │
    │                │                     │               │
    │                │                     ▼               │
LLMScore ───────────┘             HotWatchlist             │
                                       │                   │
                                       ▼                   │
TradingContext ──► BuySignal ──────► OrderRequest          │
    │                │                   │                 │
    │                ▼                   ▼                 │
MacroInsight    SellOrder          OrderResult             │
    │                │                   │                 │
    │                ▼                   ▼                 │
    └──────────► TradeRecord ◄──── Position                │
                     │                   │                 │
                     ▼                   ▼                 │
               PortfolioState ──── DailySnapshot           │
                     │                                     │
                     ▼                                     │
               SectorBudget ◄─── SectorAnalysis ◄─────────┘
```

---

*Last Updated: 2026-02-19*
