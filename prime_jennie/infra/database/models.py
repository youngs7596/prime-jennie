"""SQLModel 테이블 정의 — DB 스키마의 Single Source of Truth.

각 테이블은 SQLModel(table=True)로 정의.
도메인 모델(prime_jennie.domain)과 1:1 대응하되, DB 특화 필드(updated_at 등)를 추가.
"""

from datetime import date, datetime
from typing import Optional

from sqlalchemy import Index, UniqueConstraint
from sqlmodel import Field, SQLModel


# ─── Master Data ─────────────────────────────────────────────────


class StockMasterDB(SQLModel, table=True):
    __tablename__ = "stock_masters"

    stock_code: str = Field(primary_key=True, max_length=10)
    stock_name: str = Field(max_length=100)
    market: str = Field(default="KOSPI", max_length=10)
    market_cap: Optional[int] = None
    sector_naver: Optional[str] = Field(default=None, max_length=50)
    sector_group: Optional[str] = Field(default=None, max_length=30)
    is_active: bool = Field(default=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    __table_args__ = (
        Index("ix_stock_masters_sector", "sector_group"),
        Index("ix_stock_masters_active", "is_active", "market"),
    )


class ConfigDB(SQLModel, table=True):
    __tablename__ = "configs"

    config_key: str = Field(primary_key=True, max_length=100)
    config_value: str = Field(max_length=10000)
    description: Optional[str] = Field(default=None, max_length=500)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ─── Market Data ─────────────────────────────────────────────────


class StockDailyPriceDB(SQLModel, table=True):
    __tablename__ = "stock_daily_prices"

    stock_code: str = Field(
        foreign_key="stock_masters.stock_code",
        primary_key=True,
        max_length=10,
    )
    price_date: date = Field(primary_key=True)
    open_price: int
    high_price: int
    low_price: int
    close_price: int
    volume: int
    change_pct: Optional[float] = None

    __table_args__ = (Index("ix_daily_prices_date", "price_date"),)


class StockInvestorTradingDB(SQLModel, table=True):
    __tablename__ = "stock_investor_tradings"

    id: Optional[int] = Field(default=None, primary_key=True)
    stock_code: str = Field(
        foreign_key="stock_masters.stock_code", max_length=10
    )
    trade_date: date
    foreign_net_buy: Optional[float] = None
    institution_net_buy: Optional[float] = None
    individual_net_buy: Optional[float] = None
    foreign_holding_ratio: Optional[float] = None

    __table_args__ = (
        UniqueConstraint(
            "stock_code", "trade_date", name="uq_investor_code_date"
        ),
        Index("ix_investor_date", "trade_date", "stock_code"),
    )


class StockFundamentalDB(SQLModel, table=True):
    __tablename__ = "stock_fundamentals"

    stock_code: str = Field(
        foreign_key="stock_masters.stock_code",
        primary_key=True,
        max_length=10,
    )
    trade_date: date = Field(primary_key=True)
    per: Optional[float] = None
    pbr: Optional[float] = None
    roe: Optional[float] = None
    market_cap: Optional[int] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ─── Analysis Data ───────────────────────────────────────────────


class DailyQuantScoreDB(SQLModel, table=True):
    __tablename__ = "daily_quant_scores"

    id: Optional[int] = Field(default=None, primary_key=True)
    score_date: date
    stock_code: str = Field(
        foreign_key="stock_masters.stock_code", max_length=10
    )
    stock_name: str = Field(max_length=100)
    total_quant_score: float
    momentum_score: float
    quality_score: float
    value_score: float
    technical_score: float
    news_score: float
    supply_demand_score: float
    llm_score: Optional[float] = None
    hybrid_score: Optional[float] = None
    risk_tag: Optional[str] = Field(default=None, max_length=30)
    trade_tier: Optional[str] = Field(default=None, max_length=10)
    is_tradable: bool = True
    is_final_selected: bool = False
    llm_reason: Optional[str] = Field(default=None, max_length=2000)

    __table_args__ = (
        UniqueConstraint(
            "score_date", "stock_code", name="uq_quant_date_code"
        ),
        Index("ix_quant_final", "is_final_selected", "score_date"),
    )


class StockNewsSentimentDB(SQLModel, table=True):
    __tablename__ = "stock_news_sentiments"

    id: Optional[int] = Field(default=None, primary_key=True)
    stock_code: str = Field(
        foreign_key="stock_masters.stock_code", max_length=10
    )
    news_date: date
    press: Optional[str] = Field(default=None, max_length=100)
    headline: str = Field(max_length=500)
    summary: Optional[str] = Field(default=None, max_length=2000)
    sentiment_score: float
    sentiment_reason: Optional[str] = Field(default=None, max_length=2000)
    category: Optional[str] = Field(default=None, max_length=50)
    article_url: str = Field(max_length=1000)
    published_at: Optional[datetime] = None
    source: Optional[str] = Field(default=None, max_length=20)

    __table_args__ = (
        UniqueConstraint("article_url", name="uq_news_url"),
        Index("ix_news_code_date", "stock_code", "news_date"),
    )


class DailyMacroInsightDB(SQLModel, table=True):
    __tablename__ = "daily_macro_insights"

    insight_date: date = Field(primary_key=True)
    sentiment: str = Field(max_length=30)
    sentiment_score: int
    regime_hint: str = Field(max_length=200)
    sectors_to_favor: Optional[str] = Field(default=None, max_length=500)
    sectors_to_avoid: Optional[str] = Field(default=None, max_length=500)
    position_size_pct: int = Field(default=100)
    stop_loss_adjust_pct: int = Field(default=100)
    political_risk_level: str = Field(default="low", max_length=10)
    political_risk_summary: Optional[str] = Field(
        default=None, max_length=2000
    )
    vix_value: Optional[float] = None
    vix_regime: Optional[str] = Field(default=None, max_length=20)
    usd_krw: Optional[float] = None
    kospi_index: Optional[float] = None
    kosdaq_index: Optional[float] = None
    sector_signals_json: Optional[str] = None
    key_themes_json: Optional[str] = None
    risk_factors_json: Optional[str] = None
    raw_council_output_json: Optional[str] = None
    council_cost_usd: Optional[float] = None
    data_completeness_pct: Optional[int] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class GlobalMacroSnapshotDB(SQLModel, table=True):
    __tablename__ = "global_macro_snapshots"

    snapshot_date: date = Field(primary_key=True)
    fed_rate: Optional[float] = None
    treasury_2y: Optional[float] = None
    treasury_10y: Optional[float] = None
    treasury_spread: Optional[float] = None
    us_cpi_yoy: Optional[float] = None
    us_unemployment: Optional[float] = None
    vix: Optional[float] = None
    vix_regime: Optional[str] = Field(default=None, max_length=20)
    dxy_index: Optional[float] = None
    usd_krw: Optional[float] = None
    bok_rate: Optional[float] = None
    kospi_index: Optional[float] = None
    kospi_change_pct: Optional[float] = None
    kosdaq_index: Optional[float] = None
    kosdaq_change_pct: Optional[float] = None
    kospi_foreign_net: Optional[float] = None
    kosdaq_foreign_net: Optional[float] = None
    kospi_institutional_net: Optional[float] = None
    kospi_retail_net: Optional[float] = None
    completeness_pct: Optional[float] = None
    data_sources_json: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ─── Trading ─────────────────────────────────────────────────────


class PositionDB(SQLModel, table=True):
    __tablename__ = "positions"

    stock_code: str = Field(
        primary_key=True,
        foreign_key="stock_masters.stock_code",
        max_length=10,
    )
    stock_name: str = Field(max_length=100)
    quantity: int
    average_buy_price: int
    total_buy_amount: int
    sector_group: Optional[str] = Field(default=None, max_length=30)
    high_watermark: Optional[int] = None
    stop_loss_price: Optional[int] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class TradeLogDB(SQLModel, table=True):
    __tablename__ = "trade_logs"

    id: Optional[int] = Field(default=None, primary_key=True)
    stock_code: str = Field(
        foreign_key="stock_masters.stock_code", max_length=10
    )
    stock_name: str = Field(max_length=100)
    trade_type: str = Field(max_length=10)
    quantity: int
    price: int
    total_amount: int
    reason: str = Field(max_length=500)
    strategy_signal: Optional[str] = Field(default=None, max_length=50)
    market_regime: Optional[str] = Field(default=None, max_length=20)
    llm_score: Optional[float] = None
    hybrid_score: Optional[float] = None
    trade_tier: Optional[str] = Field(default=None, max_length=10)
    profit_pct: Optional[float] = None
    profit_amount: Optional[int] = None
    holding_days: Optional[int] = None
    trade_timestamp: datetime = Field(default_factory=datetime.utcnow)

    __table_args__ = (
        Index("ix_trade_logs_code_time", "stock_code", "trade_timestamp"),
        Index("ix_trade_logs_type_time", "trade_type", "trade_timestamp"),
    )


class DailyAssetSnapshotDB(SQLModel, table=True):
    __tablename__ = "daily_asset_snapshots"

    snapshot_date: date = Field(primary_key=True)
    total_asset: int
    cash_balance: int
    stock_eval_amount: int
    total_profit_loss: Optional[int] = None
    realized_profit_loss: Optional[int] = None
    net_investment: Optional[int] = None
    position_count: int = 0


class WatchlistHistoryDB(SQLModel, table=True):
    __tablename__ = "watchlist_histories"

    snapshot_date: date = Field(primary_key=True)
    stock_code: str = Field(
        primary_key=True,
        foreign_key="stock_masters.stock_code",
        max_length=10,
    )
    stock_name: str = Field(max_length=100)
    llm_score: Optional[float] = None
    hybrid_score: Optional[float] = None
    is_tradable: bool = True
    trade_tier: Optional[str] = Field(default=None, max_length=10)
    risk_tag: Optional[str] = Field(default=None, max_length=30)
    rank: Optional[int] = None
