# 05. Data Pipelines — prime-jennie

> 5개 핵심 파이프라인의 단계별 입출력 타입, 변환 규칙, 에러 처리 명세.
> 모든 단계의 입출력은 `prime_jennie.domain` 모델로 타입이 보장됨.

## 1. Scout → Buy 파이프라인 (메인)

### 1.1 전체 흐름

```
Airflow (08:30-14:30, 1h) → POST /trigger
  │
  ▼
Phase 1: Universe Loading
  Input:  DB query (stock_masters)
  Output: Dict[StockCode, StockMaster]
  │
  ▼
Phase 2: Data Enrichment (병렬)
  Input:  Dict[StockCode, StockMaster]
  Output: Dict[StockCode, EnrichedCandidate]
  │  ├─ KIS snapshots (8 workers)
  │  ├─ Qdrant 뉴스 검색 (8 workers)
  │  ├─ DB 수급 데이터
  │  └─ 섹터 모멘텀
  │
  ▼
Phase 3: Quant Scoring
  Input:  EnrichedCandidate
  Output: QuantScore (per stock)
  │
  ▼
Phase 4: LLM Analysis (Unified Analyst)
  Input:  QuantScore + EnrichedCandidate
  Output: HybridScore (per stock)
  │
  ▼
Phase 5: Watchlist Selection
  Input:  List[HybridScore] + SectorBudget + TradingContext
  Output: HotWatchlist → Redis
  │
  ▼ (Redis hot_watchlist → buy-scanner 로드)
  │
Phase 6: Signal Detection (실시간)
  Input:  HotWatchlist + WebSocket 틱
  Output: BuySignal → Redis Stream
  │
  ▼ (stream:buy-signals → buy-executor 소비)
  │
Phase 7: Order Execution
  Input:  BuySignal + PortfolioState
  Output: OrderResult → TradeRecord (DB)
```

### 1.2 Phase 1: Universe Loading

```python
# Input
query: SELECT * FROM stock_masters WHERE is_active = True

# Output
candidates: Dict[StockCode, StockMaster]
# 예: {"005930": StockMaster(stock_code="005930", stock_name="삼성전자", ...)}

# 필터링
# - market_cap >= MIN_MARKET_CAP (ENV)
# - KOSPI200 + KOSDAQ 상위
# - is_active = True
# 예상 출력: ~200개 종목
```

### 1.3 Phase 2: Data Enrichment

```python
class EnrichedCandidate(BaseModel):
    """Phase 2 출력 — 팩터 분석에 필요한 모든 데이터"""
    master: StockMaster
    snapshot: Optional[StockSnapshot] = None
    daily_prices: List[DailyPrice] = []           # 최근 150일
    news_context: Optional[str] = None             # Qdrant top-5 요약
    sentiment_score: Optional[Score] = None         # 0-100
    sector_momentum: Optional[float] = None         # %
    investor_trading: Optional[InvestorTradingSummary] = None
    financial_trend: Optional[FinancialTrend] = None  # v2

class InvestorTradingSummary(BaseModel):
    foreign_net_buy: float                          # 억원
    institution_net_buy: float
    individual_net_buy: float
    foreign_holding_ratio: Optional[float] = None   # %
    foreign_ratio_trend: Optional[float] = None     # % 변화

class FinancialTrend(BaseModel):
    roe_5yr_trend: Optional[float] = None           # 기울기
    operating_margin_trend: Optional[float] = None
```

**에러 처리**:
- KIS snapshot 실패 → `snapshot = None`, Quant Scorer에서 해당 팩터 0점
- 뉴스 검색 실패 → `news_context = None`, LLM에 "뉴스 데이터 없음" 전달
- 수급 데이터 실패 → `investor_trading = None`, supply_demand_score = 0

### 1.4 Phase 3: Quant Scoring

```python
# Input
candidate: EnrichedCandidate
kospi_prices: List[DailyPrice]  # 벤치마크

# Output
score: QuantScore  # domain/scoring.py

# 변환 규칙 (v2)
# momentum_score:     RSI, MACD, 가격 모멘텀 (0-20)
# quality_score:      ROE 트렌드, 재무 건전성 (0-20)
# value_score:        PER 할인, PBR 평가 (0-20)
# technical_score:    지지/저항, 브레이크아웃 (0-10)
# news_score:         감성 모멘텀 (0-10)
# supply_demand_score: 외인/기관 매수 추세 (0-20)
# total = sum(all) → 0-100
```

### 1.5 Phase 4: LLM Analysis

```python
# Input
quant: QuantScore
candidate: EnrichedCandidate
context: TradingContext

# LLM 호출 (REASONING tier)
llm_response: AnalystResponse = await llm.generate_json(
    prompt=build_analyst_prompt(quant, candidate, context),
    schema=ANALYST_RESPONSE_SCHEMA,
    service="scout",
)

class AnalystResponse(BaseModel):
    score: Score       # 0-100 (raw)
    grade: str         # S/A/B/C/D
    reason: str        # 100자+

# 후처리
clamped = clamp(llm_response.score, quant.total_score - 15, quant.total_score + 15)
risk_tag = classify_risk_tag(quant)
is_tradable = risk_tag != RiskTag.DISTRIBUTION_RISK and clamped >= 40
trade_tier = TradeTier.BLOCKED if not is_tradable else classify_tier(clamped)

# Output
hybrid: HybridScore  # domain/scoring.py
```

### 1.6 Phase 5: Watchlist Selection

```python
# Input
scores: List[HybridScore]     # 전체 후보 (is_valid=True만)
budget: SectorBudget           # 동적 섹터 예산
context: TradingContext        # Council 선호/회피 섹터

# 알고리즘: Greedy Selection
# 1. hybrid_score 내림차순 정렬
# 2. 순회하면서 섹터 cap 체크
# 3. cap 미달이면 선정, 초과면 스킵
# 4. MAX_WATCHLIST_SIZE까지

# Council 오버라이드
# context.avoid_sectors → COOL 강제
# context.favor_sectors → COOL→WARM 승격

# Output
watchlist: HotWatchlist  # domain/watchlist.py
# → Redis watchlist:active 에 저장
```

### 1.7 Phase 6: Signal Detection

```python
# Input (실시간)
watchlist: HotWatchlist          # Redis에서 로드 (60초 간격)
tick: PriceTick                  # WebSocket

class PriceTick(BaseModel):
    stock_code: StockCode
    price: int
    volume: int
    timestamp: datetime

# 처리
# 1. BarAggregator: tick → 1분봉 (OHLCV)
# 2. 기술 지표 계산: RSI, VWAP, MA
# 3. Risk Gates 체크 (9단계)
# 4. 전략 매칭

# Output
signal: BuySignal  # domain/trading.py
# → stream:buy-signals 에 발행
```

### 1.8 Phase 7: Order Execution

```python
# Input
signal: BuySignal                   # Stream에서 소비
portfolio: PortfolioState           # DB + Redis
context: TradingContext             # Redis 캐시

# 안전장치 (순차 체크)
# 1. Emergency stop → reject
# 2. 이미 보유 → reject
# 3. 중복 주문 (10분) → reject
# 4. BLOCKED tier → reject (Scout Veto)
# 5. Hard floor (score < 40) → reject
# 6. Portfolio Guard (섹터 cap + 현금 하한) → reject

# 포지션 사이징
sizing: PositionSizingResult = calculate_quantity(
    PositionSizingRequest(
        stock_code=signal.stock_code,
        stock_price=signal.signal_price,
        atr=get_atr(signal.stock_code),
        available_cash=portfolio.cash_balance,
        portfolio_value=portfolio.total_asset,
        llm_score=signal.llm_score,
        trade_tier=signal.trade_tier,
        sector_group=get_sector_group(signal.stock_code),
        held_sector_groups=list(portfolio.sector_distribution.keys()),
        position_multiplier=signal.position_multiplier,
    )
)

# 주문
order: OrderRequest = OrderRequest(
    stock_code=signal.stock_code,
    quantity=sizing.quantity,
    order_type=OrderType.LIMIT if signal.signal_type in MOMENTUM_STRATEGIES else OrderType.MARKET,
    price=align_to_tick(signal.signal_price * 1.003) if limit else None,
)

# Output
result: OrderResult = await kis_gateway.place_buy_order(order)
record: TradeRecord = TradeRecord(...)  # DB 저장
```

## 2. 매크로 Council 파이프라인

```
enhanced_macro_collection (07:00, 12:00, 18:00)
  │
  ▼
GlobalSnapshot 수집 (Naver Finance, yfinance, Fed API)
  Output: GlobalSnapshot → DB global_macro_snapshots + Redis
  │
  ▼ (macro_council DAG, 07:30/11:50)
  │
Stage 1: Strategist (DeepSeek v3.2)
  Input:  GlobalSnapshot + 정치 뉴스 + 섹터 모멘텀
  Output: StrategistAnalysis
  │
  ▼
Stage 2: Risk Analyst (DeepSeek v3.2)
  Input:  StrategistAnalysis + GlobalSnapshot
  Output: RiskAnalysis
  │
  ▼
Stage 3: Chief Judge (Claude Opus Extended Thinking)
  Input:  StrategistAnalysis + RiskAnalysis
  Output: MacroInsight → DB daily_macro_insights + Redis
  │
  ▼
TradingContext 생성 → Redis regime:current
  │
  ▼
모든 서비스가 TradingContext 소비
```

**비용**: ~$0.215/회 ($0.43/일)

## 3. 매도 파이프라인

```
price-monitor (WebSocket 실시간 틱)
  │
  ▼
보유 포지션별 가격 체크
  │
  ├─ 이익 목표 도달 → SellOrder(reason=PROFIT_TARGET)
  ├─ 손절가 도달   → SellOrder(reason=STOP_LOSS)
  ├─ 트레일링 스탑 → SellOrder(reason=TRAILING_STOP)
  ├─ RSI 과열     → SellOrder(reason=RSI_OVERBOUGHT)
  └─ 보유 기간 초과 → SellOrder(reason=TIME_EXIT)
  │
  ▼ (stream:sell-orders)
  │
sell-executor 소비
  │
  ├─ 보유 확인
  ├─ 중복 체크 (10분 윈도우)
  ├─ Redis 락 (30초)
  ├─ KIS Gateway 매도 주문
  ├─ TradeRecord 기록 (수익률 포함)
  └─ Position 삭제 + 상태 정리
```

## 4. 시장 국면 파이프라인

```
Scout 실행 시 + enhanced_macro_quick (1시간 간격)
  │
  ▼
MarketRegimeDetector.detect(kospi_prices, current_price)
  Input:  150일 KOSPI OHLCV + 현재가
  Output: MarketRegime + context_dict
  │
  ▼
Redis regime:current 저장 (TTL 1h)
  │
  ▼
모든 서비스가 참조
  ├─ buy-scanner: 전략 선택
  ├─ buy-executor: 프리셋 적용
  ├─ sell-executor: 손절폭 조정
  └─ portfolio-guard: 현금 하한선
```

## 5. 뉴스/감성 파이프라인

```
뉴스 크롤러 (네이버, 다음, 등)
  │
  ▼
NewsArticle 수집
  │
  ▼
LLM 감성 분석 (FAST tier, 로컬 vLLM)
  Input:  NewsArticle
  Output: NewsSentiment (score 0-100 + reason)
  │
  ▼
DB stock_news_sentiments 저장 (article_url UNIQUE)
  │
  ▼
Qdrant 벡터 저장 (KURE-v1 임베딩)
  │
  ▼
Scout Phase 2에서 검색 (RAG)
```

## 6. 섹터 예산 파이프라인

```
Scout Phase 5 실행 시
  │
  ▼
섹터별 모멘텀 집계
  Input:  Dict[StockCode, EnrichedCandidate]
  세분류(79) → 대분류(14) 집계
  │
  ▼
Percentile 기반 티어 배정
  p75 & >0% → HOT (cap=5)
  p25~p75   → WARM (cap=3)
  p25 & <0% → COOL (cap=2)
  │
  ▼
Council 오버라이드
  avoid_sectors → COOL 강제
  favor_sectors → COOL→WARM 승격
  │
  ▼
SectorBudget → Redis budget:sector:active (TTL 24h)
  │
  ▼
buy-executor: Portfolio Guard에서 동적 cap 적용
```

## 7. 에러 전파 규칙

### 7.1 단계별 에러 영향

| 단계 | 에러 | 영향 범위 | 대응 |
|------|------|----------|------|
| Phase 1 (Universe) | DB 연결 실패 | 전체 중단 | 재시도 3회 → 알림 |
| Phase 2 (Enrichment) | KIS API 실패 | 해당 종목 스킵 | snapshot=None 허용 |
| Phase 2 (Enrichment) | Qdrant 실패 | 뉴스 없이 진행 | news_context=None |
| Phase 3 (Quant) | 계산 오류 | 해당 종목 스킵 | is_valid=False |
| Phase 4 (LLM) | LLM 타임아웃 | 해당 종목 스킵 | CloudFailover 활성화 |
| Phase 5 (Selection) | Redis 실패 | 워치리스트 미갱신 | 이전 버전 유지 (24h TTL) |
| Phase 6 (Detection) | WebSocket 끊김 | 시그널 미발생 | 자동 재연결 + 알림 |
| Phase 7 (Execution) | KIS 주문 실패 | 해당 주문 실패 | 로그 + 알림, 재시도 없음 |
| Macro Council | LLM 실패 | 인사이트 미갱신 | Fallback (이전 데이터) |

### 7.2 데이터 무결성 보장

```python
# 모든 서비스 경계에서 Pydantic 검증
# Scanner → Stream → Executor 경로:

# Scanner (발행 시)
signal = BuySignal(...)                     # 검증 1
await stream.publish(signal)

# Executor (소비 시)
signal = BuySignal.model_validate_json(payload)  # 검증 2
# → 필드 누락/타입 불일치 시 즉시 ValidationError
# → 잘못된 메시지는 ACK + dead letter 로그
```

## 8. 모니터링 포인트

| 파이프라인 | 메트릭 | 알림 조건 |
|-----------|--------|----------|
| Scout→Buy | 워치리스트 갱신 간격 | 2시간 미갱신 |
| Scout→Buy | 시그널 발생 수 (일) | 0건 (장중) |
| Macro Council | 인사이트 생성 시간 | 08:30까지 미생성 |
| Sell | 매도 실행 지연 | 시그널→실행 >30초 |
| Market Regime | 국면 변경 | BEAR/STRONG_BEAR 진입 |

---

*Last Updated: 2026-02-19*
