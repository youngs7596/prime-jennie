# 04. Service Contracts — prime-jennie

> 서비스 간 HTTP API, Redis Stream 메시지, Redis Cache 프로토콜 정의.
> 모든 계약은 `prime_jennie.domain` Pydantic 모델 기반.

## 1. HTTP API 계약

### 1.1 kis-gateway (:8080)

**역할**: KIS API 중앙 프록시 (레이트 리밋 19req/s, 서킷 브레이커)

```
POST /api/market/snapshot
  Request:  { "stock_code": "005930" }
  Response: StockSnapshot (domain/stock.py)

POST /api/market/daily-prices
  Request:  { "stock_code": "005930", "days": 150 }
  Response: List[DailyPrice]

GET  /api/market/is-trading-day?date=2026-02-19
  Response: { "is_trading_day": true }

GET  /api/market/is-market-open
  Response: { "is_open": true, "session": "regular" }

POST /api/trading/buy
  Request:  OrderRequest (domain/trading.py)
  Response: OrderResult

POST /api/trading/sell
  Request:  OrderRequest
  Response: OrderResult

POST /api/trading/cancel
  Request:  { "order_no": "0001234567" }
  Response: { "success": true }

POST /api/account/balance
  Response: PortfolioState (domain/portfolio.py)

POST /api/account/cash
  Response: { "cash_balance": 5000000 }

GET  /health
  Response: HealthStatus (domain/health.py)
```

### 1.2 scout-job (:8087)

```
POST /trigger
  Request:  { "source": "airflow" | "manual" }
  Response: { "job_id": "scout-20260219-0830", "status": "started" }

GET  /status
  Response: {
    "current_phase": "quant_scoring" | "llm_analysis" | "selection" | "idle",
    "progress_pct": 45,
    "last_completed_at": "2026-02-19T08:30:00Z"
  }

GET  /health
  Response: HealthStatus
```

### 1.3 buy-scanner (:8081)

```
GET  /health
  Response: HealthStatus + {
    "watchlist_loaded": true,
    "stock_count": 20,
    "market_regime": "BULL",
    "active_strategies": ["GOLDEN_CROSS", "RSI_REBOUND", "MOMENTUM"]
  }
```

### 1.4 buy-executor (:8082)

```
GET  /health
  Response: HealthStatus + {
    "in_flight_orders": 0,
    "portfolio_guard_enabled": true,
    "emergency_stop": false
  }
```

### 1.5 sell-executor (:8083)

```
GET  /health
  Response: HealthStatus + {
    "in_flight_orders": 0,
    "emergency_stop": false
  }
```

### 1.6 price-monitor (:8088)

```
GET  /health
  Response: HealthStatus + {
    "monitoring_count": 5,
    "websocket_connected": true,
    "last_tick_at": "2026-02-19T10:15:00Z"
  }

POST /start
  Response: { "status": "started", "codes": ["005930", "000660"] }

POST /stop
  Response: { "status": "stopped" }
```

### 1.7 macro-council (:8089)

```
POST /trigger
  Request:  { "source": "airflow" | "manual" }
  Response: { "job_id": "council-20260219-0730", "status": "started" }

GET  /health
  Response: HealthStatus
```

### 1.8 dashboard-api (:8090)

```
# 포트폴리오
GET  /api/portfolio/summary     → PortfolioState
GET  /api/portfolio/positions   → List[Position]
GET  /api/portfolio/history     → List[DailySnapshot]

# 매크로
GET  /api/macro/insight         → MacroInsight
GET  /api/macro/regime          → { "regime": "BULL", "context": {...} }

# 워치리스트
GET  /api/watchlist/current     → HotWatchlist
GET  /api/watchlist/history     → List[WatchlistHistoryDB]

# 거래
GET  /api/trades/recent         → List[TradeRecord]
GET  /api/trades/performance    → { "win_rate": 0.64, "avg_return": 2.3 }

# LLM
GET  /api/llm/stats/{date}     → { "scout": {...}, "council": {...} }

# 시스템
GET  /api/system/health         → Dict[str, HealthStatus]

# WebSocket
WS   /ws                        → 실시간 업데이트 (가격, 시그널, 거래)
```

## 2. Redis Stream 프로토콜

### 2.1 stream:buy-signals

| 속성 | 값 |
|------|-----|
| Producer | buy-scanner |
| Consumer Group | `group:buy-executor` |
| 메시지 모델 | `BuySignal` (domain/trading.py) |
| 직렬화 | `signal.model_dump_json()` |
| 역직렬화 | `BuySignal.model_validate_json(payload)` |
| ACK 정책 | ACK-before-process (at-most-once) |
| MAXLEN | ~100,000 |
| Pending 복구 | 300초 후 재클레임 |

**메시지 필드** (BuySignal):
```json
{
  "stock_code": "005930",
  "stock_name": "삼성전자",
  "signal_type": "GOLDEN_CROSS",
  "signal_price": 72100,
  "llm_score": 70.0,
  "hybrid_score": 70.0,
  "is_tradable": true,
  "trade_tier": "TIER1",
  "risk_tag": "NEUTRAL",
  "market_regime": "BULL",
  "source": "scanner",
  "timestamp": "2026-02-19T10:15:00+00:00",
  "rsi_value": 62.0,
  "volume_ratio": 1.8,
  "vwap": 71950.0,
  "position_multiplier": 1.0
}
```

### 2.2 stream:sell-orders

| 속성 | 값 |
|------|-----|
| Producer | price-monitor |
| Consumer Group | `group:sell-executor` |
| 메시지 모델 | `SellOrder` (domain/trading.py) |
| ACK 정책 | ACK-before-process |
| MAXLEN | ~100,000 |

**메시지 필드** (SellOrder):
```json
{
  "stock_code": "005930",
  "stock_name": "삼성전자",
  "sell_reason": "PROFIT_TARGET",
  "current_price": 73500,
  "quantity": 30,
  "timestamp": "2026-02-19T13:45:00+00:00",
  "buy_price": 72100,
  "profit_pct": 1.94,
  "holding_days": 3
}
```

### 2.3 Stream 클라이언트 인터페이스

```python
# prime_jennie/infra/redis/streams.py

class TypedStreamPublisher(Generic[T]):
    """타입 안전 스트림 발행자"""
    def __init__(self, redis: Redis, stream: str, model_type: Type[T]):
        self.redis = redis
        self.stream = stream
        self.model_type = model_type

    async def publish(self, message: T) -> str:
        """Pydantic 모델을 직렬화하여 발행"""
        payload = message.model_dump_json()
        msg_id = await self.redis.xadd(
            self.stream,
            {"payload": payload},
            maxlen=100_000,
            approximate=True,
        )
        return msg_id

class TypedStreamConsumer(Generic[T]):
    """타입 안전 스트림 소비자"""
    def __init__(self, redis: Redis, stream: str, group: str, consumer: str, model_type: Type[T]):
        ...

    async def consume(self) -> AsyncIterator[T]:
        """메시지를 Pydantic 모델로 역직렬화하여 반환"""
        async for msg_id, data in self._read_stream():
            try:
                model = self.model_type.model_validate_json(data["payload"])
                await self.redis.xack(self.stream, self.group, msg_id)
                yield model
            except ValidationError as e:
                logger.error(f"[Stream] 메시지 역직렬화 실패: {e}", extra={"stream": self.stream, "msg_id": msg_id})
                await self.redis.xack(self.stream, self.group, msg_id)  # 잘못된 메시지 스킵
```

## 3. Redis Cache 프로토콜

### 3.1 키 목록

| 키 | 모델 | TTL | 소유자 | 비고 |
|----|------|-----|--------|------|
| `watchlist:active` | `HotWatchlist` | 24h | scout | 버저닝 제거, 직접 저장 |
| `macro:insight:{date}` | `MacroInsight` | 24h | council | 날짜별 캐시 |
| `regime:current` | `TradingContext` | 1h | scout | 시장 국면 |
| `budget:sector:active` | `SectorBudget` | 24h | scout | 동적 섹터 예산 |
| `llm:stats:{date}:{svc}` | Hash | 90d | LLM providers | 사용 통계 |
| `lock:buy:{code}` | "1" | 180s | buyer | 분산 락 |
| `lock:sell:{code}` | "1" | 30s | seller | 분산 락 |
| `emergency:stop` | "1" | - | manual | 긴급 정지 |

### 3.2 캐시 읽기/쓰기 패턴

```python
# prime_jennie/infra/redis/cache.py

class TypedCache(Generic[T]):
    """타입 안전 Redis 캐시"""
    def __init__(self, redis: Redis, key_pattern: str, model_type: Type[T], default_ttl: int):
        ...

    async def get(self, **key_params) -> Optional[T]:
        key = self.key_pattern.format(**key_params)
        data = await self.redis.get(key)
        if data is None:
            return None
        return self.model_type.model_validate_json(data)

    async def set(self, value: T, ttl: Optional[int] = None, **key_params) -> None:
        key = self.key_pattern.format(**key_params)
        await self.redis.set(key, value.model_dump_json(), ex=ttl or self.default_ttl)

# 사용 예시
watchlist_cache = TypedCache(redis, "watchlist:active", HotWatchlist, ttl=86400)
insight_cache = TypedCache(redis, "macro:insight:{date}", MacroInsight, ttl=86400)

# 타입 안전 읽기
watchlist: Optional[HotWatchlist] = await watchlist_cache.get()
insight: Optional[MacroInsight] = await insight_cache.get(date="2026-02-19")
```

## 4. 계약 테스트 전략

### 4.1 자동 계약 검증

```python
# tests/contract/test_stream_contracts.py

def test_buy_signal_serialization_roundtrip():
    """BuySignal이 직렬화/역직렬화를 거쳐도 동일"""
    signal = BuySignal(
        stock_code="005930",
        stock_name="삼성전자",
        signal_type=SignalType.GOLDEN_CROSS,
        signal_price=72100,
        llm_score=70.0,
        hybrid_score=70.0,
        is_tradable=True,
        trade_tier=TradeTier.TIER1,
        risk_tag=RiskTag.NEUTRAL,
        market_regime=MarketRegime.BULL,
        timestamp=datetime.now(timezone.utc),
    )
    json_str = signal.model_dump_json()
    restored = BuySignal.model_validate_json(json_str)
    assert signal == restored

def test_blocked_tier_must_be_not_tradable():
    """BLOCKED 티어는 is_tradable=False 강제"""
    with pytest.raises(ValidationError):
        BuySignal(
            stock_code="005930",
            stock_name="test",
            signal_type=SignalType.GOLDEN_CROSS,
            signal_price=100,
            llm_score=50,
            hybrid_score=50,
            is_tradable=True,     # ← BLOCKED인데 True → 에러
            trade_tier=TradeTier.BLOCKED,
            market_regime=MarketRegime.BULL,
            timestamp=datetime.now(timezone.utc),
        )
```

### 4.2 Watchlist Pipeline 계약

```python
def test_watchlist_entry_has_all_scanner_fields():
    """WatchlistEntry가 Scanner에 필요한 모든 필드를 포함"""
    entry = WatchlistEntry(
        stock_code="005930", stock_name="삼성전자",
        llm_score=70, hybrid_score=70, rank=1,
        is_tradable=True, trade_tier=TradeTier.TIER1,
    )
    # Scanner가 읽는 필드 목록
    scanner_fields = {"stock_code", "stock_name", "llm_score", "hybrid_score",
                      "is_tradable", "trade_tier", "risk_tag", "veto_applied"}
    actual_fields = set(entry.model_fields.keys())
    missing = scanner_fields - actual_fields
    assert missing == set(), f"Scanner 필수 필드 누락: {missing}"
```

### 4.3 KIS Gateway 계약

```python
def test_order_request_valid_limit():
    """지정가 주문은 price 필수"""
    with pytest.raises(ValidationError):
        OrderRequest(
            stock_code="005930",
            quantity=30,
            order_type=OrderType.LIMIT,
            price=None,  # ← limit인데 price 없음 → 에러
        )
```

## 5. 에러 처리 프로토콜

### 5.1 HTTP 에러 응답

```python
class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None
    service: str
    timestamp: datetime

# 표준 HTTP 에러 코드
# 400: 잘못된 요청 (Pydantic ValidationError)
# 404: 리소스 없음
# 409: 충돌 (이미 보유, 중복 주문)
# 429: 레이트 리밋
# 500: 내부 에러
# 503: 서비스 불가 (서킷 브레이커 open)
```

### 5.2 Stream 에러 처리

```python
# 역직렬화 실패 → ACK + 로그 (dead letter)
# 비즈니스 로직 실패 → ACK + 로그 (재시도 불가)
# 인프라 실패 (Redis/DB) → NACK + 재시도 (pending 복구)
```

---

*Last Updated: 2026-02-19*
