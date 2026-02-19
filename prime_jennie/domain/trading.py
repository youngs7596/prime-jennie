"""트레이딩 시그널 및 주문 모델."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from .enums import (
    MarketRegime,
    OrderType,
    RiskTag,
    SectorGroup,
    SellReason,
    SignalType,
    TradeTier,
    TradeType,
)
from .types import Multiplier, Quantity, Score, StockCode


class BuySignal(BaseModel):
    """매수 시그널 (Scanner → Executor, Redis Stream 메시지)."""

    stock_code: StockCode
    stock_name: str
    signal_type: SignalType
    signal_price: int  # 시그널 발생 시점 가격
    llm_score: Score
    hybrid_score: Score
    is_tradable: bool = True
    trade_tier: TradeTier
    risk_tag: RiskTag = RiskTag.NEUTRAL
    market_regime: MarketRegime
    source: str = "scanner"  # scanner | conviction | manual
    timestamp: datetime
    # 기술 지표 컨텍스트
    rsi_value: Optional[float] = None
    volume_ratio: Optional[float] = None
    vwap: Optional[float] = None
    # 매크로 컨텍스트
    position_multiplier: Multiplier = 1.0


class SellOrder(BaseModel):
    """매도 주문 (Monitor → Executor, Redis Stream 메시지)."""

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
    """KIS Gateway 주문 요청."""

    stock_code: StockCode
    quantity: Quantity
    order_type: OrderType = OrderType.MARKET
    price: Optional[int] = None  # limit 주문 시 필수


class OrderResult(BaseModel):
    """KIS Gateway 주문 결과."""

    success: bool
    order_no: Optional[str] = None
    stock_code: StockCode
    quantity: int
    price: int
    message: Optional[str] = None


class TradeRecord(BaseModel):
    """거래 기록 (DB 저장용)."""

    stock_code: StockCode
    stock_name: str
    trade_type: TradeType
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
    """포지션 사이징 입력."""

    stock_code: StockCode
    stock_price: int
    atr: float  # Average True Range
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
    """포지션 사이징 결과."""

    quantity: int
    target_weight_pct: float  # 목표 비중 (%)
    actual_weight_pct: float  # 실제 비중 (%)
    applied_multipliers: dict[str, float]  # 적용된 배율 상세
    reasoning: str
