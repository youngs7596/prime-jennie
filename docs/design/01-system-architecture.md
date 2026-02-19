# 01. System Architecture — prime-jennie

## 1. 아키텍처 개요

```
                          ┌─────────────────────────────┐
                          │      Airflow Scheduler       │
                          │  (DAGs: scout, macro, data)  │
                          └──────────┬──────────────────┘
                                     │ HTTP trigger
         ┌───────────────────────────┼───────────────────────────┐
         ▼                           ▼                           ▼
┌─────────────────┐        ┌─────────────────┐        ┌─────────────────┐
│   scout-job     │        │  macro-council  │        │  data-collector  │
│  (종목 발굴)    │        │ (매크로 분석)   │        │  (시세/수급)     │
│  FastAPI :8087  │        │ FastAPI :8089   │        │  Airflow Task    │
└────────┬────────┘        └────────┬────────┘        └────────┬────────┘
         │                          │                          │
         │ Redis                    │ DB + Redis               │ DB
         │ hot_watchlist            │ daily_macro_insight       │ prices, investor
         ▼                          ▼                          ▼
┌─────────────────┐        ┌─────────────────────────────────────────────┐
│   buy-scanner   │◄───────│              Redis                          │
│  (매수 시그널)  │        │  Streams: buy-signals, sell-orders           │
│  FastAPI :8081  │        │  Cache: watchlist, macro, regime, budget     │
└────────┬────────┘        │  Locks: buy:{code}, sell:{code}              │
         │                 └──────────────────────────────────────────────┘
         │ stream:buy-signals            ▲           │
         ▼                               │           ▼
┌─────────────────┐            ┌─────────────────┐  ┌─────────────────┐
│  buy-executor   │            │  price-monitor  │  │  sell-executor  │
│  (매수 실행)    │            │ (가격 감시)     │  │  (매도 실행)    │
│  FastAPI :8082  │            │  FastAPI :8088  │  │  FastAPI :8083  │
└────────┬────────┘            └────────┬────────┘  └────────┬────────┘
         │                              │                     │
         └──────────────────────────────┼─────────────────────┘
                                        ▼
                              ┌─────────────────┐
                              │   kis-gateway   │
                              │  (한투 API)     │
                              │  FastAPI :8080  │
                              └─────────────────┘
                                        │
                              ┌─────────────────┐
                              │  dashboard-api  │
                              │  FastAPI :8090  │
                              │  + frontend :80 │
                              └─────────────────┘
```

## 2. 서비스 카탈로그

### 2.1 핵심 트레이딩 서비스

| 서비스 | 포트 | 역할 | 입력 | 출력 |
|--------|------|------|------|------|
| **kis-gateway** | 8080 | KIS API 중앙 프록시 (레이트 리밋, 서킷 브레이커) | HTTP 요청 | KIS API 응답 |
| **scout-job** | 8087 | AI 종목 발굴 (Quant + LLM) | Airflow 트리거 | Redis: HotWatchlist |
| **buy-scanner** | 8081 | 실시간 매수 기회 탐색 | WebSocket 틱 + HotWatchlist | Stream: BuySignal |
| **buy-executor** | 8082 | 매수 주문 실행 | Stream: BuySignal | DB: TradeLog |
| **sell-executor** | 8083 | 매도 주문 실행 | Stream: SellOrder | DB: TradeLog |
| **price-monitor** | 8088 | 보유 종목 가격 감시 | WebSocket 틱 + Portfolio | Stream: SellOrder |

### 2.2 분석 서비스

| 서비스 | 포트 | 역할 | 스케줄 |
|--------|------|------|--------|
| **macro-council** | 8089 | 3단계 매크로 분석 | 07:30, 11:50 KST |
| **data-collector** | - | 시세/수급/재무 수집 | Airflow DAGs |
| **daily-briefing** | 8086 | 일일 브리핑 생성 | 17:00 KST |

### 2.3 지원 서비스

| 서비스 | 포트 | 역할 |
|--------|------|------|
| **dashboard-api** | 8090 | REST API + WebSocket |
| **dashboard-frontend** | 80 | React SPA |
| **command-handler** | 8091 | 텔레그램 명령 |

### 2.4 인프라

| 서비스 | 포트 | 프로파일 |
|--------|------|----------|
| **vllm-llm** | 8001 | infra (EXAONE 4.0 32B) |
| **vllm-embed** | 8002 | infra (KURE-v1) |
| **mariadb** | 3307 | infra |
| **redis** | 6379 | infra |
| **qdrant** | 6333 | infra |
| **airflow** | 8085 | infra |

## 3. 통신 패턴

### 3.1 동기 (HTTP)

- **Airflow → 서비스**: DAG 트리거 (POST /trigger)
- **서비스 → kis-gateway**: 주문/시세 조회
- **dashboard → 서비스**: 상태 조회

모든 HTTP 통신은 **FastAPI + Pydantic 자동 검증**.

### 3.2 비동기 (Redis Streams)

| 스트림 | Producer | Consumer | 모델 |
|--------|----------|----------|------|
| `stream:buy-signals` | buy-scanner | buy-executor | `BuySignal` |
| `stream:sell-orders` | price-monitor | sell-executor | `SellOrder` |

**메시지 프로토콜**:
```python
# 발행 (producer)
signal = BuySignal(stock_code="005930", ...)
await stream.publish("stream:buy-signals", signal.model_dump_json())

# 소비 (consumer)
async for msg in stream.consume("stream:buy-signals", group="buy-executor"):
    signal = BuySignal.model_validate_json(msg.payload)
    # → 필드 누락 시 여기서 즉시 ValidationError
    result = await process_buy_signal(signal)
```

**Consumer Group 정책**:
- ACK-before-process (at-most-once) — 현재 my-prime-jennie와 동일
- Pending 자동 복구 (300초 후 재클레임)
- MAXLEN 100,000 (무한 증가 방지)

### 3.3 공유 상태 (Redis Cache)

| 키 패턴 | 타입 | TTL | 소유자 | 소비자 |
|---------|------|-----|--------|--------|
| `watchlist:active` | HotWatchlist JSON | 24h | scout | scanner |
| `macro:insight:{date}` | MacroInsight JSON | 24h | council | all |
| `regime:current` | MarketRegime JSON | 1h | scout | all |
| `budget:sector:active` | SectorBudget JSON | 24h | scout | executor, guard |
| `llm:stats:{date}:{svc}` | Hash | 90d | LLM providers | dashboard |

**Redis 키 관리**:
```python
class RedisKeys(str, Enum):
    """모든 Redis 키를 하나의 Enum으로 관리"""
    WATCHLIST_ACTIVE = "watchlist:active"
    MACRO_INSIGHT = "macro:insight:{date}"
    REGIME_CURRENT = "regime:current"
    SECTOR_BUDGET = "budget:sector:active"
    BUY_LOCK = "lock:buy:{code}"
    SELL_LOCK = "lock:sell:{code}"
    LLM_STATS = "llm:stats:{date}:{service}"
```

## 4. 데이터 저장소

### 4.1 MariaDB (영구 저장)

**역할**: 거래 기록, 분석 결과, 마스터 데이터
**접근**: SQLModel + async session (서비스별 connection pool)

핵심 테이블 그룹:
- **마스터**: stock_master, sector_mapping
- **포트폴리오**: active_portfolio, trade_log, daily_asset_snapshot
- **분석**: daily_quant_score, daily_macro_insight, watchlist_history
- **시장 데이터**: stock_daily_price, stock_minute_price, stock_investor_trading
- **뉴스**: stock_news_sentiment
- **설정**: config

### 4.2 Redis (실시간 상태 + 메시징)

**역할**: 캐시, 스트림, 분산 락, 실시간 가격
**접근**: redis-py async + Pydantic 직렬화

### 4.3 Qdrant (벡터 검색)

**역할**: 뉴스 RAG (의미 검색)
**접근**: qdrant-client + KURE-v1 임베딩 (vllm-embed)

## 5. 서비스 격리 원칙

### 5.1 공유 코드 범위

```
prime_jennie/domain/    → 모든 서비스가 의존 (읽기 전용 데이터 모델)
prime_jennie/infra/     → 인프라 클라이언트 (선택적 의존)
services/*/             → 서비스별 비즈니스 로직 (격리됨)
```

### 5.2 서비스별 Docker 이미지

```dockerfile
# 모든 서비스의 공통 패턴
FROM python:3.12-slim

# 1. 의존성 (드물게 변경)
COPY pyproject.toml /app/
RUN pip install /app[service-name]

# 2. 도메인 모델 (가끔 변경)
COPY prime_jennie/domain/ /app/prime_jennie/domain/

# 3. 인프라 (가끔 변경)
COPY prime_jennie/infra/ /app/prime_jennie/infra/

# 4. 서비스 코드 (자주 변경)
COPY prime_jennie/services/scout/ /app/prime_jennie/services/scout/
```

### 5.3 서비스 간 직접 호출 금지

```
BAD:  buy-executor가 scout-job의 내부 함수를 직접 import
GOOD: buy-executor는 Redis Stream의 BuySignal 메시지만 소비
```

**예외**: kis-gateway는 HTTP API로만 접근 (REST 클라이언트)

## 6. 장애 대응

### 6.1 서비스별 복원력

| 서비스 | 장애 시 동작 | 복구 방법 |
|--------|-------------|----------|
| kis-gateway 다운 | 모든 주문 실패, 시세 불가 | 서킷 브레이커 → 자동 재시도 |
| scout-job 실패 | 이전 워치리스트 유지 (24h TTL) | 다음 시간 자동 재실행 |
| buy-scanner 다운 | 매수 시그널 미발생 | 재시작 시 워치리스트 재로드 |
| buy-executor 다운 | 시그널 스트림에 누적 | 재시작 시 pending 자동 소비 |
| macro-council 실패 | 이전 인사이트 유지 | 다음 스케줄 자동 재실행 |
| Redis 다운 | 실시간 기능 전체 정지 | Redis 복구 필수 (HA 구성) |
| MariaDB 다운 | 주문 기록 불가 | DB 복구 필수 |

### 6.2 헬스 체크

```python
# 모든 서비스의 공통 헬스 체크 패턴
@app.get("/health")
async def health() -> HealthStatus:
    return HealthStatus(
        service="buy-executor",
        status="healthy",
        uptime_seconds=get_uptime(),
        dependencies={
            "redis": await check_redis(),
            "db": await check_db(),
            "kis_gateway": await check_gateway(),
        }
    )
```

### 6.3 긴급 정지

```python
# Redis 플래그 기반 (즉시 전파)
class EmergencyStop:
    TRADING_PAUSE = "emergency:trading_pause"    # 매매 일시 중지
    FULL_STOP = "emergency:full_stop"            # 전체 중지
```

## 7. 배포 구성

### 7.1 Docker Compose 프로파일

| 프로파일 | 서비스 | 용도 |
|----------|--------|------|
| `infra` | mariadb, redis, qdrant, vllm-llm, vllm-embed, airflow | 인프라 |
| `trading` | kis-gateway, scout, scanner, buyer, seller, monitor | 트레이딩 |
| `support` | dashboard-api, dashboard-frontend, command-handler | 지원 |
| `analysis` | macro-council, daily-briefing, data-collector | 분석 |

### 7.2 리소스 할당

| 서비스 | CPU | Memory | GPU |
|--------|-----|--------|-----|
| vllm-llm | 4 cores | 8GB | 90% VRAM |
| vllm-embed | 1 core | 2GB | 5% VRAM |
| scout-job | 2 cores | 4GB | - |
| buy-scanner | 1 core | 1GB | - |
| buy-executor | 1 core | 512MB | - |
| kis-gateway | 2 cores | 1GB | - |
| 기타 | 1 core | 512MB | - |

---

*Last Updated: 2026-02-19*
