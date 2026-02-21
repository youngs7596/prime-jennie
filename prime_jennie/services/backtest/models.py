"""백테스트 데이터 모델 — 설정, 시뮬레이션 포지션, 트레이드 로그, 스냅샷."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from prime_jennie.domain.enums import (
    MarketRegime,
    SectorGroup,
    SellReason,
    SignalType,
    TradeTier,
)


@dataclass
class BacktestConfig:
    """백테스트 실행 설정."""

    start_date: date
    end_date: date
    initial_capital: int = 50_000_000  # 5천만 원
    buy_fee_pct: float = 0.015  # 매수 수수료 0.015%
    sell_fee_pct: float = 0.195  # 매도 수수료+세금 0.195%
    slippage_pct: float = 0.1  # 슬리피지 0.1%
    export_csv_dir: str | None = None


@dataclass
class SimPosition:
    """시뮬레이션 보유 포지션."""

    stock_code: str
    stock_name: str
    quantity: int
    buy_price: int  # 평균 매수가 (수수료+슬리피지 반영 전 원가)
    buy_date: date
    sector_group: SectorGroup | None = None
    signal_type: SignalType | None = None
    trade_tier: TradeTier = TradeTier.TIER1
    llm_score: float = 0.0
    hybrid_score: float = 0.0
    # 동적 상태
    high_watermark: int = 0  # 보유 중 최고가
    scale_out_level: int = 0
    rsi_sold: bool = False
    profit_floor_active: bool = False
    profit_floor_level: float = 0.0

    def __post_init__(self) -> None:
        if self.high_watermark == 0:
            self.high_watermark = self.buy_price

    @property
    def total_cost(self) -> int:
        return self.buy_price * self.quantity

    def holding_days(self, current_date: date) -> int:
        return (current_date - self.buy_date).days

    def profit_pct(self, current_price: int) -> float:
        if self.buy_price <= 0:
            return 0.0
        return (current_price - self.buy_price) / self.buy_price * 100.0

    def high_profit_pct(self) -> float:
        if self.buy_price <= 0:
            return 0.0
        return (self.high_watermark - self.buy_price) / self.buy_price * 100.0


@dataclass
class TradeLog:
    """백테스트 거래 기록."""

    trade_date: date
    stock_code: str
    stock_name: str
    trade_type: str  # "BUY" | "SELL"
    quantity: int
    price: int
    total_amount: int  # 수수료 포함 실거래 금액
    fee: int = 0
    # 매수 정보
    signal_type: SignalType | None = None
    trade_tier: TradeTier | None = None
    llm_score: float | None = None
    hybrid_score: float | None = None
    # 매도 정보
    sell_reason: SellReason | None = None
    profit_pct: float | None = None
    profit_amount: int | None = None
    holding_days: int | None = None
    regime: MarketRegime | None = None


@dataclass
class DailySnapshot:
    """일별 포트폴리오 스냅샷."""

    snapshot_date: date
    cash: int
    portfolio_value: int  # 보유 주식 평가액 (종가 기준)
    total_value: int  # cash + portfolio_value
    position_count: int
    daily_return_pct: float = 0.0
    regime: MarketRegime = MarketRegime.SIDEWAYS


@dataclass
class WatchlistEntry:
    """일별 워치리스트 항목 (메모리)."""

    stock_code: str
    stock_name: str
    snapshot_date: date
    hybrid_score: float
    llm_score: float
    trade_tier: TradeTier
    risk_tag: str = "NEUTRAL"
    rank: int = 99
    sector_group: SectorGroup | None = None


@dataclass
class DailyOHLCV:
    """일봉 가격 데이터 (메모리)."""

    price_date: date
    open_price: int
    high_price: int
    low_price: int
    close_price: int
    volume: int


@dataclass
class MacroDay:
    """일별 매크로 데이터 (메모리)."""

    insight_date: date
    sentiment: str
    regime: MarketRegime
    position_size_pct: int = 100
    stop_loss_adjust_pct: int = 100


@dataclass
class PriceCache:
    """종목별 가격 데이터 캐시 — O(1) 날짜 lookup."""

    # stock_code → date → DailyOHLCV
    by_stock_date: dict[str, dict[date, DailyOHLCV]] = field(default_factory=dict)
    # stock_code → sorted list of DailyOHLCV (oldest first)
    by_stock_sorted: dict[str, list[DailyOHLCV]] = field(default_factory=dict)

    def get(self, stock_code: str, d: date) -> DailyOHLCV | None:
        return self.by_stock_date.get(stock_code, {}).get(d)

    def get_history_until(self, stock_code: str, d: date, n: int = 60) -> list[DailyOHLCV]:
        """d 이전(포함) 최대 n개 가격 반환 (oldest first)."""
        prices = self.by_stock_sorted.get(stock_code, [])
        # bisect로 d 이하인 마지막 인덱스 찾기
        end = 0
        for i, p in enumerate(prices):
            if p.price_date <= d:
                end = i + 1
            else:
                break
        return prices[max(0, end - n) : end]

    def get_close_prices_until(self, stock_code: str, d: date, n: int = 60) -> list[float]:
        """d 이전(포함) 최대 n개 종가 반환 (oldest first)."""
        return [p.close_price for p in self.get_history_until(stock_code, d, n)]
