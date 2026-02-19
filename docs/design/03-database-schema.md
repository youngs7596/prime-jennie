# 03. Database Schema — prime-jennie

> SQLModel (SQLAlchemy + Pydantic) 기반 단일 모델 정의.
> my-prime-jennie의 30+ ORM 모델 + 3개 Raw SQL 테이블 → **정규화된 단일 체계**로 통합.

## 1. 설계 원칙

1. **SQLModel 단일 모델**: DB 테이블 = Pydantic 모델 = API 응답 (변환 없음)
2. **Raw SQL 제거**: DAILY_MACRO_INSIGHT, ENHANCED_MACRO_SNAPSHOT, STOCK_FUNDAMENTALS 모두 ORM화
3. **JSON 컬럼 최소화**: 구조화 가능한 데이터는 별도 테이블로 분리
4. **일관된 네이밍**: snake_case (Python), 테이블명은 복수형
5. **명시적 FK**: 암묵적 JOIN → 명시적 ForeignKey
6. **INDEX 선언**: 자주 조회하는 패턴에 대한 복합 인덱스
7. **레거시 제거**: NEWS_SENTIMENT 테이블 삭제, WATCHLIST 테이블→Redis 전용

## 2. 테이블 목록

### 2.1 마스터 데이터

| 테이블 | 용도 | PK | 변경 주기 |
|--------|------|-----|----------|
| `stock_masters` | 종목 마스터 | stock_code | 주간 |
| `configs` | 시스템 설정 | config_key | 수시 |

### 2.2 시장 데이터

| 테이블 | 용도 | PK | 변경 주기 |
|--------|------|-----|----------|
| `stock_daily_prices` | 일별 OHLCV (3년) | (stock_code, price_date) | 일일 |
| `stock_minute_prices` | 분봉 OHLCV | (stock_code, price_time) | 장중 5분 |
| `stock_investor_tradings` | 수급 데이터 | id | 일일 |
| `stock_fundamentals` | PER/PBR/ROE 시계열 | (stock_code, trade_date) | 일일 |

### 2.3 분석 데이터

| 테이블 | 용도 | PK | 변경 주기 |
|--------|------|-----|----------|
| `daily_quant_scores` | Quant 평가 기록 | id | Scout 실행 시 |
| `stock_news_sentiments` | 뉴스 감성 | id | 실시간 |
| `daily_macro_insights` | 매크로 인사이트 | insight_date | 일 2회 |
| `global_macro_snapshots` | 글로벌 매크로 | snapshot_date | 일 3회 |

### 2.4 트레이딩

| 테이블 | 용도 | PK | 변경 주기 |
|--------|------|-----|----------|
| `positions` | 보유 포지션 (활성) | stock_code | 매수/매도 시 |
| `trade_logs` | 거래 기록 | id | 매수/매도 시 |
| `daily_asset_snapshots` | 일일 자산 | snapshot_date | 일일 |
| `watchlist_histories` | 워치리스트 히스토리 | (snapshot_date, stock_code) | Scout 실행 시 |

### 2.5 LLM/분석 기록

| 테이블 | 용도 | PK | 변경 주기 |
|--------|------|-----|----------|
| `llm_decision_ledgers` | LLM 의사결정 기록 | id | Scout 실행 시 |
| `factor_metadata` | 팩터 성과 통계 | id | 주간 |

## 3. 테이블 정의 (SQLModel)

### 3.1 stock_masters

```python
class StockMaster(SQLModel, table=True):
    __tablename__ = "stock_masters"

    stock_code: str = Field(primary_key=True, max_length=10)
    stock_name: str = Field(max_length=100)
    market: str = Field(max_length=10)          # KOSPI | KOSDAQ
    market_cap: Optional[int] = None
    sector_naver: Optional[str] = Field(default=None, max_length=50)     # 세분류
    sector_group: Optional[str] = Field(default=None, max_length=30)     # 14개 대분류
    is_active: bool = Field(default=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Index
    __table_args__ = (
        Index("ix_stock_masters_sector", "sector_group"),
        Index("ix_stock_masters_active", "is_active", "market"),
    )
```

### 3.2 stock_daily_prices

```python
class StockDailyPrice(SQLModel, table=True):
    __tablename__ = "stock_daily_prices"

    stock_code: str = Field(foreign_key="stock_masters.stock_code", primary_key=True, max_length=10)
    price_date: date = Field(primary_key=True)
    open_price: int
    high_price: int
    low_price: int
    close_price: int
    volume: int
    change_pct: Optional[float] = None

    __table_args__ = (
        Index("ix_daily_prices_date_desc", "price_date", postgresql_ops={"price_date": "DESC"}),
    )
```

### 3.3 stock_investor_tradings

```python
class StockInvestorTrading(SQLModel, table=True):
    __tablename__ = "stock_investor_tradings"

    id: Optional[int] = Field(default=None, primary_key=True)
    stock_code: str = Field(foreign_key="stock_masters.stock_code", max_length=10)
    trade_date: date
    foreign_net_buy: Optional[float] = None        # 억원
    institution_net_buy: Optional[float] = None
    individual_net_buy: Optional[float] = None
    foreign_holding_ratio: Optional[float] = None  # %

    __table_args__ = (
        UniqueConstraint("stock_code", "trade_date", name="uq_investor_code_date"),
        Index("ix_investor_date_desc", "trade_date", "stock_code"),
    )
```

### 3.4 stock_fundamentals

```python
class StockFundamental(SQLModel, table=True):
    """PER/PBR/ROE 시계열 — my-prime-jennie의 Raw SQL DDL을 ORM화"""
    __tablename__ = "stock_fundamentals"

    stock_code: str = Field(foreign_key="stock_masters.stock_code", primary_key=True, max_length=10)
    trade_date: date = Field(primary_key=True)
    per: Optional[float] = None
    pbr: Optional[float] = None
    roe: Optional[float] = None
    market_cap: Optional[int] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)
```

### 3.5 positions (현재 보유)

```python
class PositionDB(SQLModel, table=True):
    """활성 포트폴리오 — my-prime-jennie의 ACTIVE_PORTFOLIO 대체"""
    __tablename__ = "positions"

    stock_code: str = Field(primary_key=True, foreign_key="stock_masters.stock_code", max_length=10)
    stock_name: str = Field(max_length=100)
    quantity: int
    average_buy_price: int
    total_buy_amount: int
    sector_group: Optional[str] = Field(default=None, max_length=30)
    high_watermark: Optional[int] = None       # 보유 중 최고가
    stop_loss_price: Optional[int] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
```

### 3.6 trade_logs

```python
class TradeLogDB(SQLModel, table=True):
    __tablename__ = "trade_logs"

    id: Optional[int] = Field(default=None, primary_key=True)
    stock_code: str = Field(foreign_key="stock_masters.stock_code", max_length=10)
    stock_name: str = Field(max_length=100)
    trade_type: str = Field(max_length=10)     # BUY | SELL
    quantity: int
    price: int
    total_amount: int
    reason: str = Field(max_length=500)
    strategy_signal: Optional[str] = Field(default=None, max_length=50)
    market_regime: Optional[str] = Field(default=None, max_length=20)
    llm_score: Optional[float] = None
    hybrid_score: Optional[float] = None
    trade_tier: Optional[str] = Field(default=None, max_length=10)
    # 매도 시 수익 데이터
    profit_pct: Optional[float] = None
    profit_amount: Optional[int] = None
    holding_days: Optional[int] = None
    trade_timestamp: datetime = Field(default_factory=datetime.utcnow)

    __table_args__ = (
        Index("ix_trade_logs_code_time", "stock_code", "trade_timestamp"),
        Index("ix_trade_logs_type_time", "trade_type", "trade_timestamp"),
    )
```

### 3.7 daily_quant_scores

```python
class DailyQuantScoreDB(SQLModel, table=True):
    __tablename__ = "daily_quant_scores"

    id: Optional[int] = Field(default=None, primary_key=True)
    score_date: date
    stock_code: str = Field(foreign_key="stock_masters.stock_code", max_length=10)
    stock_name: str = Field(max_length=100)
    total_quant_score: float
    momentum_score: float
    quality_score: float
    value_score: float
    technical_score: float
    news_score: float
    supply_demand_score: float
    # LLM 결과
    llm_score: Optional[float] = None
    hybrid_score: Optional[float] = None
    risk_tag: Optional[str] = Field(default=None, max_length=30)
    trade_tier: Optional[str] = Field(default=None, max_length=10)
    is_tradable: bool = True
    is_final_selected: bool = False          # 워치리스트에 최종 선정 여부
    llm_reason: Optional[str] = Field(default=None, max_length=2000)

    __table_args__ = (
        UniqueConstraint("score_date", "stock_code", name="uq_quant_date_code"),
        Index("ix_quant_final", "is_final_selected", "score_date"),
    )
```

### 3.8 daily_macro_insights (Raw SQL → ORM)

```python
class DailyMacroInsightDB(SQLModel, table=True):
    """my-prime-jennie의 Raw SQL DAILY_MACRO_INSIGHT를 ORM화"""
    __tablename__ = "daily_macro_insights"

    insight_date: date = Field(primary_key=True)
    sentiment: str = Field(max_length=30)
    sentiment_score: int
    regime_hint: str = Field(max_length=200)
    # 구조화 JSON → 별도 컬럼으로 핵심 필드 추출
    sectors_to_favor: Optional[str] = Field(default=None, max_length=500)   # JSON array
    sectors_to_avoid: Optional[str] = Field(default=None, max_length=500)
    position_size_pct: int = Field(default=100)
    stop_loss_adjust_pct: int = Field(default=100)
    political_risk_level: str = Field(default="low", max_length=10)
    political_risk_summary: Optional[str] = Field(default=None, max_length=2000)
    # 글로벌 스냅샷 핵심 수치
    vix_value: Optional[float] = None
    vix_regime: Optional[str] = Field(default=None, max_length=20)
    usd_krw: Optional[float] = None
    kospi_index: Optional[float] = None
    kosdaq_index: Optional[float] = None
    # 상세 데이터 (JSON — 대시보드 표시용)
    sector_signals_json: Optional[str] = None      # JSON
    key_themes_json: Optional[str] = None
    risk_factors_json: Optional[str] = None
    raw_council_output_json: Optional[str] = None
    # 메타
    council_cost_usd: Optional[float] = None
    data_completeness_pct: Optional[int] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)
```

### 3.9 global_macro_snapshots (Raw SQL → ORM)

```python
class GlobalMacroSnapshotDB(SQLModel, table=True):
    """my-prime-jennie의 ENHANCED_MACRO_SNAPSHOT을 ORM화"""
    __tablename__ = "global_macro_snapshots"

    snapshot_date: date = Field(primary_key=True)
    # US
    fed_rate: Optional[float] = None
    treasury_2y: Optional[float] = None
    treasury_10y: Optional[float] = None
    treasury_spread: Optional[float] = None
    us_cpi_yoy: Optional[float] = None
    us_unemployment: Optional[float] = None
    # Volatility
    vix: Optional[float] = None
    vix_regime: Optional[str] = Field(default=None, max_length=20)
    # Currency
    dxy_index: Optional[float] = None
    usd_krw: Optional[float] = None
    # Korea
    bok_rate: Optional[float] = None
    kospi_index: Optional[float] = None
    kospi_change_pct: Optional[float] = None
    kosdaq_index: Optional[float] = None
    kosdaq_change_pct: Optional[float] = None
    # Investor Flow
    kospi_foreign_net: Optional[float] = None
    kosdaq_foreign_net: Optional[float] = None
    kospi_institutional_net: Optional[float] = None
    kospi_retail_net: Optional[float] = None
    # Metadata
    completeness_pct: Optional[float] = None
    data_sources_json: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)
```

### 3.10 stock_news_sentiments

```python
class StockNewsSentimentDB(SQLModel, table=True):
    __tablename__ = "stock_news_sentiments"

    id: Optional[int] = Field(default=None, primary_key=True)
    stock_code: str = Field(foreign_key="stock_masters.stock_code", max_length=10)
    news_date: date
    press: Optional[str] = Field(default=None, max_length=100)
    headline: str = Field(max_length=500)
    summary: Optional[str] = Field(default=None, max_length=2000)
    sentiment_score: float                  # 0-100
    sentiment_reason: Optional[str] = Field(default=None, max_length=2000)
    category: Optional[str] = Field(default=None, max_length=50)
    article_url: str = Field(max_length=1000)
    published_at: Optional[datetime] = None
    source: Optional[str] = Field(default=None, max_length=20)

    __table_args__ = (
        UniqueConstraint("article_url", name="uq_news_url"),
        Index("ix_news_code_date", "stock_code", "news_date"),
    )
```

### 3.11 daily_asset_snapshots

```python
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
```

### 3.12 watchlist_histories

```python
class WatchlistHistoryDB(SQLModel, table=True):
    __tablename__ = "watchlist_histories"

    snapshot_date: date = Field(primary_key=True)
    stock_code: str = Field(primary_key=True, foreign_key="stock_masters.stock_code", max_length=10)
    stock_name: str = Field(max_length=100)
    llm_score: Optional[float] = None
    hybrid_score: Optional[float] = None
    is_tradable: bool = True
    trade_tier: Optional[str] = Field(default=None, max_length=10)
    risk_tag: Optional[str] = Field(default=None, max_length=30)
    rank: Optional[int] = None
```

### 3.13 configs

```python
class ConfigDB(SQLModel, table=True):
    __tablename__ = "configs"

    config_key: str = Field(primary_key=True, max_length=100)
    config_value: str = Field(max_length=10000)    # TEXT
    description: Optional[str] = Field(default=None, max_length=500)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
```

## 4. 삭제된 테이블 (my-prime-jennie 대비)

| 테이블 | 삭제 이유 | 대체 |
|--------|----------|------|
| NEWS_SENTIMENT | STOCK_NEWS_SENTIMENT에 통합됨 (2026-02-17) | stock_news_sentiments |
| WATCHLIST | Redis HotWatchlist로 완전 이전 | Redis `watchlist:active` |
| RAG_CACHE | Qdrant로 대체됨 | Qdrant 벡터 DB |
| OPTIMIZATION_HISTORY | 사용 안 함 | 삭제 |
| MARKET_FLOW_SNAPSHOT | 소비자 없음 (orphaned) | 삭제 |
| SHADOW_RADAR_LOG | 사용률 극저 | 삭제 (필요 시 재생성) |
| BACKTEST_TRADELOG | trade_logs에 tag로 통합 | trade_logs.is_backtest |

## 5. 인덱스 전략

### 5.1 핵심 쿼리별 인덱스

| 쿼리 패턴 | 테이블 | 인덱스 |
|-----------|--------|--------|
| 최근 N일 종가 조회 | stock_daily_prices | (stock_code, price_date DESC) |
| 종목별 최신 뉴스 | stock_news_sentiments | (stock_code, news_date DESC) |
| 선정 종목 히스토리 | daily_quant_scores | (is_final_selected, score_date DESC) |
| 전략별 거래 성과 | trade_logs | (strategy_signal, trade_timestamp DESC) |
| 섹터별 수급 추이 | stock_investor_tradings | (trade_date DESC, stock_code) |

### 5.2 파티셔닝 후보 (데이터 증가 시)

| 테이블 | 파티션 키 | 시기 |
|--------|----------|------|
| stock_daily_prices | price_date (연도별) | 3년+ 데이터 |
| stock_minute_prices | price_time (월별) | 3개월+ 데이터 |
| trade_logs | trade_timestamp (분기별) | 1년+ 데이터 |

## 6. 마이그레이션 계획

### Phase 1: 스키마 생성 (alembic)
```bash
alembic init migrations
alembic revision --autogenerate -m "initial schema"
alembic upgrade head
```

### Phase 2: 데이터 이관
```python
# my-prime-jennie DB → prime-jennie DB
# 1. stock_masters (STOCK_MASTER → stock_masters)
# 2. stock_daily_prices (STOCK_DAILY_PRICES_3Y → stock_daily_prices)
# 3. positions (ACTIVE_PORTFOLIO → positions)
# 4. trade_logs (TRADELOG → trade_logs)
# 5. daily_asset_snapshots (DAILY_ASSET_SNAPSHOT → daily_asset_snapshots)
# 6. stock_news_sentiments (STOCK_NEWS_SENTIMENT → stock_news_sentiments)
# 7. daily_macro_insights (DAILY_MACRO_INSIGHT Raw SQL → daily_macro_insights ORM)
```

### Phase 3: 검증
- 레코드 수 일치 확인
- PK/FK 무결성 확인
- 샘플 데이터 정합성 확인

---

*Last Updated: 2026-02-19*
