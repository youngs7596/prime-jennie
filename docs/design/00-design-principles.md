# 00. Design Principles — prime-jennie

> my-prime-jennie 2개월 운영에서 얻은 교훈을 바탕으로 한 설계 원칙

## 1. 왜 재설계하는가

my-prime-jennie는 my-little-jennie → my-super-jennie → my-ultra-jennie를 거치며
기존 구조에 큰 변경 없이 확장/개선/신기능을 추가해왔다.
GPT, Gemini, Claude, DeepSeek 등 여러 LLM이 각자의 사상으로 수정하면서
**체계적이지 못한 아키텍처**가 됐다.

### 1.1 핵심 문제: "배선 불량"

설계는 야심차고 구조도 잘 나뉘어 있지만, **서비스 간 데이터 연결**이 반복적으로 끊어짐.

| 사건 | 근본 원인 | 결과 |
|------|----------|------|
| Council 섹터 신호 미적용 | `except ImportError: pass`로 aiohttp 실패 무시 | 2달간 Council 데이터 Scout에 미반영 |
| 선호 섹터 0개 매칭 | 세분류↔대분류 substring 매칭 | 반도체/IT ≠ 반도체와반도체장비 |
| is_tradable 하드코딩 | `publish_signal()`에서 `True` 고정 | Scout Veto Power 관통 |
| hybrid_score 유실 | signal dict에 필드 미포함 | Executor에서 항상 0 |
| llm_score 미전달 | `calculate_quantity()` 호출 시 누락 | A+ 비중 18%→12%로 하향 |
| cancel_order 미구현 | KISGatewayClient에 메서드 부재 | Ghost purchase (지정가 미취소) |

**공통점**: 모든 버그가 **dict 기반 데이터 전달**에서 발생.
타입 체크 없이 `dict.get("field", default)`로 전달하면서 필드명 오타, 누락, 타입 불일치가 런타임까지 감지 불가.

### 1.2 아키텍처 평가: 67/100

| 항목 | 점수 | 설명 |
|------|------|------|
| 서비스 분리 | 9/10 | 명확한 책임 경계 |
| 데이터 모델 | 4/10 | dict 기반, 타입 없음, 필드 유실 반복 |
| 에러 처리 | 3/10 | Silent failure 패턴 만연 |
| 설정 관리 | 7/10 | 계층적 config, 다만 109개 키 과다 |
| 테스트 | 7/10 | 1400+개, 하지만 통합 갭 못 잡음 |
| LLM 통합 | 8/10 | CloudFailover, 비용 최적화 우수 |
| 매매 로직 | 8/10 | Quant v2 + Unified Analyst 검증됨 |
| 운영 성숙도 | 6/10 | 로깅은 풍부하지만 구조화 부족 |

---

## 2. 핵심 설계 원칙

### P1. Typed Contracts at Every Boundary

> **서비스 경계에서 반드시 Pydantic 모델로 데이터를 주고받는다.**

```python
# BAD (my-prime-jennie 방식)
signal = {
    "stock_code": "005930",
    "llm_score": 70,
    "hybrid_score": 70,
    # is_tradable 누락? trade_tier 누락? 런타임까지 모름
}

# GOOD (prime-jennie 방식)
class BuySignal(BaseModel):
    stock_code: StockCode          # Annotated[str, Field(pattern=r'^\d{6}$')]
    stock_name: str
    signal_type: SignalType         # Enum
    signal_price: PositiveFloat
    llm_score: Score               # Annotated[float, Field(ge=0, le=100)]
    hybrid_score: Score
    is_tradable: bool
    trade_tier: TradeTier          # Literal["TIER1", "TIER2", "BLOCKED"]
    risk_tag: RiskTag              # Literal["BULLISH", "NEUTRAL", "CAUTION", "DISTRIBUTION_RISK"]
    market_regime: MarketRegime    # Enum
    timestamp: datetime

signal = BuySignal(**data)  # 필드 누락 → 즉시 ValidationError
```

**적용 범위**:
- Redis Stream 메시지 (publish/consume 양쪽)
- REST API 요청/응답 (FastAPI 자동 문서화)
- DB 읽기/쓰기 (SQLModel = SQLAlchemy + Pydantic)
- 서비스 내부 함수 간 데이터 전달

### P2. Fail Fast, Fail Loud

> **에러를 삼키지 않는다. 경계에서 검증하고, 내부에서는 신뢰한다.**

```python
# BAD (my-prime-jennie 방식)
try:
    from shared.macro_insight import get_enhanced_trading_context
    trading_context = get_enhanced_trading_context()
except ImportError:
    logger.debug("macro_insight not available")  # 2달간 감지 못함
    trading_context = None

# GOOD (prime-jennie 방식)
# 1. 필수 의존성은 import 시점에 검증 (컨테이너 시작 시 실패)
from prime_jennie.domain.macro import TradingContext

# 2. 선택적 데이터는 명시적 Optional + 로깅
context = macro_service.get_trading_context()
if context is None:
    logger.warning("[MacroContext] 데이터 로드 실패 — 기본값 적용")
    context = TradingContext.default()
```

**규칙**:
- `except Exception: pass` 금지 — 최소 warning 로그
- `except ImportError` → 컨테이너 빌드 시 import 검증으로 대체
- 외부 API 실패 → 명시적 fallback 값 + 구조화 로그
- 내부 함수 간 → Pydantic 검증 통과하면 이후 None 체크 불필요

### P3. Single Source of Truth

> **하나의 데이터는 하나의 장소에서만 정의하고, 나머지는 참조만 한다.**

| 데이터 | Source of Truth | 참조 방식 |
|--------|----------------|----------|
| 종목 마스터 | `stock_master` DB 테이블 | stock_code FK |
| 섹터 분류 | `sector_naver` 컬럼 + `NAVER_TO_GROUP` | 함수 호출 |
| 매크로 인사이트 | `daily_macro_insight` DB | Redis 캐시 (읽기 전용) |
| 시장 국면 | `market_regime` Redis 캐시 | 1시간 TTL |
| 설정값 | `registry.py` 기본값 → ENV 오버라이드 | `config.get()` |

**anti-pattern**: PER/PBR/ROE가 WATCHLIST, STOCK_FUNDAMENTALS, FINANCIAL_METRICS_QUARTERLY 3곳에 중복 → **하나로 통합**.

### P4. Explicit Over Implicit

> **암묵적 동작보다 명시적 선언을 우선한다.**

```python
# BAD: 암묵적 기본값 (my-prime-jennie)
sector_momentum = info.get("sector_momentum", 0)  # 실패인지 실제 0인지 구분 불가
sentiment_score = info.get("sentiment_score", 50)  # 50이 실제 중립인지 fallback인지?

# GOOD: 명시적 Optional 처리
class StockAnalysis(BaseModel):
    sector_momentum: Optional[float] = None  # None = 데이터 없음, 0.0 = 실제 0
    sentiment_score: Optional[Score] = None  # None = 분석 안 됨

    @property
    def has_sentiment(self) -> bool:
        return self.sentiment_score is not None
```

### P5. Minimal Surface Area

> **서비스 간 인터페이스를 최소화하고, 내부 구현은 자유롭게 한다.**

```
my-prime-jennie:  38개 shared 모듈 + 14개 하위 디렉토리
                  모든 서비스가 shared/ 전체를 COPY

prime-jennie:     domain/ (Pydantic 모델만, 로직 없음)
                  각 서비스가 domain/ 패키지만 의존
                  서비스 내부 로직은 각 서비스 패키지에 캡슐화
```

**shared/ 정리 방향**:
- `shared/` → `prime_jennie/domain/` (순수 데이터 모델 + 열거형)
- 서비스별 로직 → 각 서비스 패키지 내부
- 공용 유틸리티 → `prime_jennie/infra/` (DB, Redis, LLM 클라이언트)

### P6. Configuration as Code

> **설정은 코드에서 타입으로 정의하고, 환경변수로 오버라이드한다.**

```python
# BAD: 109개 키를 dict에 나열
REGISTRY = {
    "MAX_PORTFOLIO_SIZE": {"default": 10, "type": "int", "category": "risk"},
    "CASH_FLOOR_BULL_PCT": {"default": 10.0, "type": "float", "category": "risk"},
    ...
}

# GOOD: Pydantic Settings
class RiskConfig(BaseSettings):
    max_portfolio_size: int = 10
    cash_floor_bull_pct: float = 10.0
    cash_floor_bear_pct: float = 25.0
    portfolio_guard_enabled: bool = True

    class Config:
        env_prefix = "RISK_"
```

### P7. Observability by Default

> **모든 서비스 경계 호출에 구조화 로그 + 메트릭을 기본 장착한다.**

```python
# 모든 서비스 경계에 자동 로깅
@log_boundary(service="buy-executor", action="process_signal")
async def process_buy_signal(signal: BuySignal) -> BuyResult:
    ...

# 구조화 로그 (JSON)
{
    "timestamp": "2026-02-19T10:15:00Z",
    "service": "buy-executor",
    "action": "process_signal",
    "stock_code": "005930",
    "signal_type": "GOLDEN_CROSS",
    "result": "executed",
    "duration_ms": 234,
    "context": {"llm_score": 70, "trade_tier": "TIER1"}
}
```

---

## 3. my-prime-jennie에서 가져올 것

### 3.1 검증된 도메인 로직 (유지)

| 구성요소 | 상태 | 비고 |
|---------|------|------|
| Quant Scorer v2 | IC=+0.095, Hit Rate 70.6% | 핵심 로직 그대로 이식 |
| Unified Analyst Pipeline | 3→1 LLM, ±15pt 가드레일 | 스키마만 Pydantic화 |
| Dynamic Sector Budget | HOT/WARM/COOL 티어 | 로직 유지, 인터페이스 정리 |
| CloudFailover Provider | OpenRouter→DeepSeek→Ollama | 에러 처리만 강화 |
| Portfolio Guard | 섹터 cap + 현금 하한선 | 로직 유지 |
| Risk Gates | 9단계 순차 체크 | 로직 유지, 타입 강화 |
| Macro Council 3단계 | 전략가→리스크→수석 | JSON 스키마 유지 |
| 네이버 섹터 분류 | 79→14 매핑 | SSOT 패턴 유지 |

### 3.2 버릴 것

| 구성요소 | 이유 |
|---------|------|
| dict 기반 데이터 전달 | Pydantic 모델로 대체 |
| `except ImportError: pass` | 빌드 시 검증으로 대체 |
| NEWS_SENTIMENT 레거시 테이블 | STOCK_NEWS_SENTIMENT만 유지 |
| 109개 Registry 키 | Pydantic Settings 그룹화 |
| shared/ 38개 모듈 flat 구조 | domain/ + infra/ 재구성 |
| subprocess+regex LLM 호출 | 직접 provider 호출 (이미 완료) |
| Deprecated 함수 export | 제거 |
| 사문화 전략 (BULL_PULLBACK 등) | 비활성 상태면 코드에서 제거 |
| Raw SQL (DAILY_MACRO_INSIGHT) | SQLModel ORM으로 통합 |

### 3.3 개선할 것

| 항목 | 현재 | 목표 |
|------|------|------|
| 서비스 간 계약 | dict + `.get()` | Pydantic BaseModel |
| DB 모델 | SQLAlchemy + Raw SQL 혼용 | SQLModel 단일 |
| 설정 관리 | registry dict + ENV + DB | Pydantic Settings |
| 에러 처리 | `except: pass` 패턴 | Fail-fast + 구조화 에러 |
| 로깅 | `logger.info(f"...")` | 구조화 JSON + 메트릭 |
| 테스트 | 계약 테스트 사후 추가 | 모델 기반 자동 검증 |
| Redis 키 | 문자열 하드코딩 | Enum + 타입 래퍼 |
| 스트림 메시지 | JSON string | Pydantic 직렬화/역직렬화 |

---

## 4. 기술 스택 결정

| 영역 | 선택 | 근거 |
|------|------|------|
| 언어 | Python 3.12+ | 기존 도메인 로직 이식 용이 |
| 타입 시스템 | Pydantic v2 + mypy strict | 서비스 경계 계약 보장 |
| DB ORM | SQLModel (SQLAlchemy + Pydantic) | 단일 모델로 DB + API |
| API 프레임워크 | FastAPI | Pydantic 네이티브 통합 |
| 메시징 | Redis Streams | 검증됨, 단순함 |
| LLM | LiteLLM + Pydantic | 프로바이더 추상화 표준 |
| 설정 | pydantic-settings | ENV 자동 매핑 |
| 로깅 | structlog | JSON 구조화 로그 |
| 테스트 | pytest + hypothesis | Property-based testing |
| 컨테이너 | Docker Compose | 검증됨 |
| CI/CD | Jenkins (기존) | 변경 불필요 |
| 벡터 DB | Qdrant | 검증됨 |
| 캐시 | Redis | 검증됨 |
| RDBMS | MariaDB | 검증됨 |

---

## 5. 프로젝트 구조 (예상)

```
prime-jennie/
├── prime_jennie/
│   ├── domain/              # 순수 데이터 모델 (Pydantic) — 모든 서비스가 공유
│   │   ├── __init__.py
│   │   ├── stock.py         # StockCode, StockMaster, StockSnapshot
│   │   ├── scoring.py       # QuantScore, LLMScore, HybridScore
│   │   ├── trading.py       # BuySignal, SellSignal, TradeResult
│   │   ├── portfolio.py     # Position, PortfolioState
│   │   ├── macro.py         # MacroInsight, TradingContext, MarketRegime
│   │   ├── sector.py        # SectorGroup, SectorBudget, SectorTier
│   │   ├── watchlist.py     # WatchlistEntry, HotWatchlist
│   │   ├── news.py          # NewsSentiment, NewsArticle
│   │   ├── config.py        # RiskConfig, ScoringConfig, StrategyConfig
│   │   └── enums.py         # MarketRegime, TradeTier, RiskTag, SignalType
│   │
│   ├── infra/               # 인프라 클라이언트 (DB, Redis, LLM, KIS)
│   │   ├── database/        # SQLModel 기반 DB 연결 + Repository
│   │   ├── redis/           # Redis 클라이언트 + Stream 추상화
│   │   ├── llm/             # LLM Provider + Factory
│   │   ├── kis/             # KIS Gateway 클라이언트
│   │   └── observability/   # 구조화 로깅 + 메트릭
│   │
│   └── services/            # 마이크로서비스
│       ├── scout/           # AI 종목 발굴
│       ├── scanner/         # 실시간 매수 기회 감시
│       ├── buyer/           # 매수 주문 실행
│       ├── seller/          # 매도 주문 실행
│       ├── monitor/         # 실시간 가격 모니터링
│       ├── gateway/         # KIS API 게이트웨이
│       ├── council/         # 매크로 Council 분석
│       ├── dashboard/       # 대시보드 API + 프론트엔드
│       └── scheduler/       # Airflow DAGs
│
├── tests/
│   ├── unit/                # 단위 테스트 (서비스별)
│   ├── contract/            # 계약 테스트 (서비스 간 인터페이스)
│   ├── integration/         # 통합 테스트 (DB, Redis 포함)
│   └── e2e/                 # End-to-end 시나리오 테스트
│
├── docs/
│   └── design/              # 이 문서들
│
├── docker-compose.yml
├── pyproject.toml           # 단일 패키지 관리 (monorepo)
└── Makefile                 # 빌드, 테스트, 배포 명령
```

---

## 6. 마이그레이션 전략

**Phase 1: 도메인 모델 정의** (1주)
- `prime_jennie/domain/` 전체 Pydantic 모델 작성
- 계약 테스트 100% 커버리지

**Phase 2: 인프라 레이어** (1주)
- DB (SQLModel), Redis, LLM 클라이언트
- 기존 my-prime-jennie와 동일 DB/Redis 접속 가능

**Phase 3: 핵심 서비스 이식** (2주)
- scout → scanner → buyer 메인 파이프라인
- 기존 도메인 로직 + 새 타입 시스템

**Phase 4: 보조 서비스 이식** (1주)
- seller, monitor, council, dashboard
- 기존 Airflow DAGs 호환

**Phase 5: 전환** (1주)
- 병행 운영 (my-prime-jennie + prime-jennie)
- 동일 입력 → 출력 비교 검증
- 스위치오버

---

*Last Updated: 2026-02-19*
