# 🤖 Prime Jennie — AI 기반 한국 주식 자율 트레이딩 시스템

<div align="center">

![Version](https://img.shields.io/badge/version-2.0.0-blue)
![Python](https://img.shields.io/badge/python-3.12-green)
![Docker](https://img.shields.io/badge/docker-compose-2496ED)
![Airflow](https://img.shields.io/badge/airflow-2.10-017CEE)
![Tests](https://img.shields.io/badge/tests-522%20passed-brightgreen)
![License](https://img.shields.io/badge/license-MIT-yellow)

**멀티 LLM 기반 한국 주식 자율 트레이딩 시스템**

*"AI가 발굴하고, 통계가 검증하고, 사람이 결정한다."*

</div>

---

## 📋 목차

- [개요](#-개요)
- [핵심 기능](#-핵심-기능)
- [시스템 아키텍처](#-시스템-아키텍처)
- [서비스 구성](#-서비스-구성)
- [기술 스택](#-기술-스택)
- [빠른 시작](#-빠른-시작)
- [프로젝트 구조](#-프로젝트-구조)
- [데이터 흐름](#-데이터-흐름)
- [Exit Rules 체계](#-exit-rules-체계)
- [설정](#-설정)
- [테스트](#-테스트)
- [모니터링](#-모니터링)

---

## 🎯 개요

**Prime Jennie**는 한국투자증권 Open API를 활용한 AI 기반 자율 트레이딩 에이전트입니다.

[my-prime-jennie](https://github.com/youngs7596/my-prime-jennie)의 후속 프로젝트로, 모놀리식 shared 모듈을 **도메인 중심 마이크로서비스 아키텍처**로 재설계하고, Pydantic v2 + SQLModel 기반의 **타입 안전한 도메인 모델**을 도입하였습니다.

### 주요 특징

| 기능 | 설명 |
|------|------|
| 🧠 **하이브리드 스코어링** | Quant Scorer v2(정량 60%) + Unified Analyst(LLM 정성 40%), ±15pt 가드레일 |
| 📊 **Macro Council** | 전략가 → 리스크분석가 → 수석심판 (3인 구조화 JSON 파이프라인) |
| 🎯 **12단계 Exit Rules** | Hard Stop → Profit Lock → Breakeven Stop → ATR → Trailing TP → Scale-Out 등 우선순위 체인 |
| ⚡ **Conviction Entry** | 고확신 종목 장 초반 선제 진입 (09:15-10:30) |
| 🔄 **KIS WebSocket** | 실시간 체결가 → Redis Stream → Scanner tick consumer |
| 📱 **텔레그램 알림** | 매수/매도 체결 실시간 알림 (Redis Stream 비동기 발송) |
| 🛡️ **Portfolio Guard** | 동적 섹터 cap + 국면별 현금 하한선 (BULL 10%, BEAR 25%) |
| 📈 **국면 연동** | BULL/SIDEWAYS/BEAR 국면별 차등 전략 (스톱, 익절, 타임아웃) |
| 📰 **뉴스 파이프라인** | 뉴스 크롤 → LLM 감성 분석 → Qdrant RAG 저장 |
| 📊 **LLM Usage Stats** | 서비스별 호출/토큰 자동 기록 → Dashboard 표시 |

---

## 🚀 핵심 기능

### 1. Scout Pipeline (종목 발굴)

```
KOSPI+KOSDAQ Universe (200종목)
       ↓
[Phase 1] Quant Scoring v2 (잠재력 기반)
   - 모멘텀20 + 품질20 + 가치20 + 기술10 + 뉴스10 + 수급20 = 100
   - Chart Phase Filter: Stage 4(하락세) 원천 차단
   - Sector Penalty: "Falling Knife" 섹터(-10점)
   - 비용: $0 (LLM 미사용)
   - 상위 25개 종목 선별
       ↓
[Phase 2] Unified Analyst (1-pass LLM, deepseek_cloud)
   - Hunter+Debate+Judge 통합 → run_analyst_scoring()
   - 코드 기반 risk_tag: classify_risk_tag(quant_result)
   - ±15pt 가드레일: llm_score = clamp(raw, quant-15, quant+15)
   - Veto Power: DISTRIBUTION_RISK → is_tradable=False
       ↓
Watchlist (상위 15개)
```

### 2. 매수/매도 파이프라인

```
[KIS WebSocket] → Redis kis:prices → [Scanner] → BuySignal (Redis Stream)
                                                        ↓
                                              [Buy Executor] → KIS Gateway → 주문
                                                        ↓
                                              [Price Monitor] → Exit Rules 평가
                                                        ↓
                                              [Sell Executor] → 매도 주문
```

### 3. Exit Rules (12단계 우선순위 체인)

```
Hard Stop(-10%) → Profit Floor → Profit Lock(ATR) → Breakeven Stop(+3%→+0.3%)
    → ATR Stop → Fixed Stop(-6%) → Trailing TP → Scale-Out → RSI Overbought
    → Target Price → Death Cross → Time Exit
```

- **Breakeven Stop**: +3% 도달 후 +0.3% 미만 시 전량 매도
- **Scale-Out**: 국면별 분할 익절 (BULL 3단계, SIDEWAYS/BEAR 4단계)
- **Time-Tightening**: 장기 보유 시 손절선 점진 축소 (BULL 15일, SIDEWAYS/BEAR 10일 시작)

### 4. Macro Council (3인 전문가 회의)

```
[매크로 데이터 수집] → [Strategist 전략가] → [Risk Analyst 리스크분석가] → [Judge 수석심판]
                                                                              ↓
                                                                 TradingContext (Redis)
                                                                 - 시장 국면 (BULL/BEAR)
                                                                 - 섹터 HOT/WARM/COOL
                                                                 - 현금 비중 권고
```

---

## 🏗 시스템 아키텍처

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          Prime Jennie System                            │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌───────────────┐    ┌───────────────┐    ┌───────────────┐           │
│  │ News Pipeline │───▶│    Qdrant     │◀───│  Scout Job    │           │
│  │ (Crawl+Analyze)    │   (RAG)       │    │ (Unified Anl) │           │
│  └───────────────┘    └───────────────┘    └───────────────┘           │
│         │                                          │                    │
│         ▼                                          ▼                    │
│  ┌───────────────┐    ┌───────────────┐    ┌───────────────┐           │
│  │    Redis      │◀───│  KIS Gateway  │───▶│  Buy Scanner  │           │
│  │(Cache+Stream) │    │ (REST+WS)     │    │ (Tick Consumer)│           │
│  └───────────────┘    └───────────────┘    └───────────────┘           │
│         │                    │                     │                    │
│         ▼                    ▼                     ▼                    │
│  ┌───────────────┐    ┌───────────────┐    ┌───────────────┐           │
│  │   MariaDB     │◀───│ Price Monitor │───▶│ Buy Executor  │           │
│  │  (SQLModel)   │    │ (Exit Rules)  │    │(Portfolio Guard)│          │
│  └───────────────┘    └───────────────┘    └───────────────┘           │
│                              │                                          │
│                              ▼                                          │
│                       ┌───────────────┐                                 │
│                       │ Sell Executor │                                 │
│                       │(Scale-Out/Stop)│                                │
│                       └───────────────┘                                 │
│                                                                         │
├─────────────────────────────────────────────────────────────────────────┤
│  Dashboard (React + FastAPI)  │  Grafana + Loki  │  Telegram Bot       │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 📦 서비스 구성

### Trading Services (profile: real)

| 서비스 | 포트 | 설명 |
|--------|------|------|
| **kis-gateway** | 8080 | KIS Securities API 게이트웨이 + WebSocket streamer |
| **buy-scanner** | 8081 | 실시간 매수 신호 탐지 (tick consumer) |
| **buy-executor** | 8082 | 매수 주문 실행 + Portfolio Guard |
| **sell-executor** | 8083 | 매도 주문 실행 (trailing stop, time exit) |
| **daily-briefing** | 8086 | 일간 리포트 생성 + Telegram 발송 |
| **scout-job** | 8087 | AI 종목 발굴 (Quant v2 + Unified Analyst) |
| **price-monitor** | 8088 | 포지션 모니터링 + 12단계 Exit Rules |
| **macro-council** | 8089 | 3인 전문가 매크로 분석 (구조화 JSON) |
| **dashboard** | 8090 | REST API (portfolio, watchlist, macro, trades, LLM stats) |
| **telegram** | 8091 | Telegram 명령 핸들러 (polling) |
| **news-pipeline** | 8092 | 뉴스 크롤 → LLM 감성 분석 → Qdrant 저장 |
| **job-worker** | 8095 | Airflow DAG 유틸리티 (데이터 수집, 정리) |
| **dashboard-frontend** | 80 | React 대시보드 UI (Nginx reverse proxy) |

### Infrastructure Services (profile: infra)

| 서비스 | 포트 | 설명 |
|--------|------|------|
| **mariadb** | 3307 | 영구 저장소 (SQLModel ORM) |
| **redis** | 6379 | 캐시, 스트림, 상태, 메시징 |
| **qdrant** | 6333 | 벡터 DB (뉴스 RAG) |
| **vllm-llm** | 8001 | EXAONE 4.0 32B AWQ (로컬 LLM 추론) |
| **vllm-embed** | 8002 | KURE-v1 (임베딩 모델) |
| **grafana** | 3300 | 모니터링 대시보드 |
| **loki** | 3100 | 로그 집계 |

### 자동화 작업 (Airflow DAGs)

| DAG | 시간 (KST) | 설명 |
|-----|------------|------|
| **scout_pipeline** | 평일 08:30-15:30, 1시간 | AI 종목 발굴 |
| **macro_collection** | 평일 07:00, 12:00, 18:00 | 글로벌 매크로 수집 |
| **macro_council** | 평일 07:30 | 3인 매크로 분석 |
| **price_monitor_ops** | 평일 09:00/15:30 | 가격 모니터 시작/중지 |
| **daily_briefing** | 평일 17:00 | 브리핑 Telegram 발송 |
| **daily_asset_snapshot** | 평일 15:45 | 일일 자산 스냅샷 |
| **data_collection** | 평일 16:00-18:45 | 일봉, 수급, DART 공시 수집 |
| **data_cleanup_weekly** | 일 03:00 | 오래된 데이터 정리 |

---

## 🛠 기술 스택

### 백엔드
- **Python 3.12** — 핵심 언어
- **FastAPI** — REST API (Pydantic v2 자동 검증)
- **SQLModel** — ORM (SQLAlchemy 2.0 + Pydantic v2)
- **Redis Streams** — 서비스 간 비동기 메시징

### AI / ML
- **vLLM v0.15.1** — 로컬 LLM 추론 (EXAONE 4.0 32B AWQ)
- **KURE-v1** — 한국어 임베딩 모델
- **DeepSeek Cloud** — REASONING/THINKING 티어 (failover)
- **Anthropic Claude / Google Gemini** — 보조 분석, 검증
- **Qdrant** — 벡터 저장소 (뉴스 RAG)

### 데이터
- **MariaDB** — 영구 저장소 (SQLModel ORM, Alembic 마이그레이션)
- **Redis** — 캐시, 실시간 상태, 스트림 메시징

### 프론트엔드
- **React 18 + TypeScript** — Dashboard UI
- **Vite** — 빌드 도구
- **Tailwind CSS** — 스타일링
- **Recharts + TanStack Query** — 차트, 데이터 페칭

### 인프라
- **Docker Compose** — 22개 서비스 (infra + real 프로파일)
- **Airflow** — DAG 기반 워크플로우 스케줄러
- **GitHub Actions** — CI/CD (lint + test + deploy)
- **Grafana + Loki** — 모니터링 + 로그 집계

---

## 🚀 빠른 시작

### 사전 요구사항

- Docker & Docker Compose
- Python 3.12+
- NVIDIA GPU (RTX 3090/4090 권장, vLLM 로컬 추론용)
- uv (Python 패키지 매니저)

### 1. 환경 설정

```bash
# 저장소 클론
git clone https://github.com/youngs7596/prime-jennie.git
cd prime-jennie

# 개발 환경 설정
uv sync --dev

# 환경변수 파일 생성
cp .env.example .env
# .env 편집하여 API 키, DB 접속 정보 입력
```

### 2. 테스트 실행

```bash
# 전체 테스트
uv run pytest tests/ -v

# 린트 + 포맷
uv run ruff check .
uv run ruff format
```

### 3. 서비스 실행

```bash
# 인프라 서비스 (vLLM 부팅 ~2분 소요)
docker compose --profile infra up -d

# 애플리케이션 서비스
docker compose --profile real up -d --build
```

---

## 📁 프로젝트 구조

```
prime-jennie/
├── prime_jennie/
│   ├── domain/           # 도메인 모델 (30+ Pydantic v2 models)
│   │   ├── enums.py     # MarketRegime, SectorGroup, SignalType, SellReason
│   │   ├── stock.py     # StockMaster, StockSnapshot, DailyPrice
│   │   ├── portfolio.py # Position, PortfolioState, DailySnapshot
│   │   ├── scoring.py   # HybridScore, QuantScore, QuantSubScores
│   │   ├── macro.py     # MacroInsight, TradingContext
│   │   ├── watchlist.py # HotWatchlist, WatchlistEntry
│   │   ├── signals.py   # BuySignal, SellOrder
│   │   ├── trading.py   # OrderRequest, OrderResult, PositionSizingRequest
│   │   └── config.py    # AppConfig (Pydantic Settings, env prefix 기반)
│   ├── infra/            # 인프라 어댑터
│   │   ├── database/    # SQLModel ORM, repositories, Alembic migrations
│   │   ├── redis/       # TypedCache[T], TypedStreamPublisher/Consumer
│   │   ├── llm/         # Provider factory (vLLM, DeepSeek, Claude, Gemini)
│   │   ├── kis/         # KIS API client (Gateway proxy)
│   │   ├── crawlers/    # Naver sector crawler
│   │   └── observability/ # Structured logging, LLM usage metrics
│   └── services/         # 마이크로서비스 (FastAPI apps)
│       ├── base.py      # App factory (create_app) + common /health
│       ├── deps.py      # FastAPI Depends (Redis, DB session, KIS client)
│       ├── gateway/     # KIS REST API proxy + WebSocket streamer
│       ├── scout/       # AI scoring pipeline
│       ├── scanner/     # Real-time buy signal (tick consumer + strategies)
│       ├── buyer/       # Buy execution (Portfolio Guard, position sizing)
│       ├── seller/      # Sell execution
│       ├── monitor/     # Price monitoring + 12-rule Exit Rules
│       ├── council/     # Macro council (3-expert structured JSON)
│       ├── news/        # News pipeline (crawl → analyze → archive)
│       ├── dashboard/   # Dashboard REST API (6 routers)
│       ├── briefing/    # Daily report + Telegram send
│       ├── telegram/    # Telegram bot (polling + command handler)
│       └── jobs/        # Airflow utility jobs
├── frontend/             # React 18 + TypeScript + Vite + Tailwind
├── dags/                 # Airflow DAGs (scout, macro, utility, monitor)
├── prompts/              # LLM prompt templates
├── migrations/           # Alembic DB migrations
├── infra/                # Loki/Grafana config
├── tests/                # Unit (522 passed)
├── .ai/                  # AI assistant rules + session handoffs
├── .github/workflows/    # CI/CD (lint, test, deploy)
├── docker-compose.yml    # 22 services (infra + real profiles)
└── pyproject.toml        # Dependencies + tool config (uv)
```

---

## 🔄 데이터 흐름

```
[Macro Collection] → [Macro Council] → TradingContext (Redis)
                                              ↓
[Scout Pipeline] Universe → Enrich → Quant v2 → LLM Analyst → Watchlist (Redis)
                                                                    ↓
[KIS WebSocket] → Redis kis:prices → [Scanner] → BuySignal (Redis Stream)
                                                        ↓
                                              [Buy Executor] → KIS Gateway → 주문
                                                        ↓
                                              [Price Monitor] → [Sell Executor] → 매도
```

---

## 🛡 Exit Rules 체계

12개 규칙이 우선순위 체인으로 평가됩니다. 첫 번째 매칭 규칙이 실행됩니다.

| 순위 | 규칙 | 조건 | 매도 비율 |
|------|------|------|----------|
| 1 | **Hard Stop** | profit ≤ -10% | 100% |
| 2 | **Profit Floor** | 고점 대비 급락 (floor 활성) | 100% |
| 3 | **Profit Lock** | ATR 기반 동적 이익 보호 (L1/L2) | 100% |
| 4 | **Breakeven Stop** | +3% 도달 후 +0.3% 미만 | 100% |
| 5 | **ATR Stop** | 매수가 - ATR×2 이하 | 100% |
| 6 | **Fixed Stop** | profit ≤ -6% (Time-Tightening 적용) | 100% |
| 7 | **Trailing TP** | 고점 대비 -3.5% 하락 | 100% |
| 8 | **Scale-Out** | 국면별 분할 익절 단계 | 15~25% |
| 9 | **RSI Overbought** | RSI ≥ 75 & profit ≥ 3% | 50% |
| 10 | **Target Price** | 목표가 도달 | 100% |
| 11 | **Death Cross** | 데드크로스 & 손실 중 | 100% |
| 12 | **Time Exit** | 국면별 최대 보유일 초과 | 100% |

### Scale-Out 단계 (국면별)

| 국면 | 단계 | 설명 |
|------|------|------|
| **BULL** | 3단계 | +7.0%(25%), +15.0%(25%), +25.0%(15%) |
| **SIDEWAYS** | 4단계 | +3.0%(25%), +7.0%(25%), +12.0%(25%), +18.0%(15%) |
| **BEAR** | 4단계 | +2.0%(25%), +5.0%(25%), +8.0%(25%), +12.0%(15%) |

---

## ⚙️ 설정

환경변수 기반 설정 (Pydantic Settings, env prefix 자동 매핑):

| Prefix | Config Class | 예시 |
|--------|-------------|------|
| `DB_` | DatabaseConfig | `DB_HOST`, `DB_PORT`, `DB_NAME` |
| `REDIS_` | RedisConfig | `REDIS_HOST`, `REDIS_PORT` |
| `KIS_` | KISConfig | `KIS_APP_KEY`, `KIS_GATEWAY_URL` |
| `LLM_` | LLMConfig | `LLM_TIER_FAST_PROVIDER`, `LLM_VLLM_LLM_URL` |
| `RISK_` | RiskConfig | `RISK_MAX_PORTFOLIO_SIZE`, `RISK_MAX_BUY_COUNT_PER_DAY` |
| `SCORING_` | ScoringConfig | `SCORING_QUANT_SCORER_VERSION` |
| `SCANNER_` | ScannerConfig | `SCANNER_CONVICTION_ENTRY_ENABLED` |
| `SELL_` | SellConfig | `SELL_TRAILING_ENABLED`, `SELL_STOP_LOSS_PCT` |
| `INFRA_` | InfraConfig | `INFRA_QDRANT_URL` |

### Docker Compose 프로파일

| 프로파일 | 목적 | 비고 |
|----------|------|------|
| `infra` | 인프라 서비스 | MariaDB, Redis, Qdrant, vLLM, Grafana, Loki |
| `real` | 실거래 운영 | 모든 트레이딩 서비스 (infra 필요) |

```bash
# 인프라 먼저 실행
docker compose --profile infra up -d

# 애플리케이션 서비스
docker compose --profile real up -d --build
```

---

## 🧪 테스트

```bash
# 전체 테스트 (522 passed)
uv run pytest tests/ -v --tb=short

# Unit 테스트만
uv run pytest tests/unit/ -v

# 특정 서비스
uv run pytest tests/unit/services/test_exit_rules.py -v

# 커버리지
uv run pytest tests/ --cov=prime_jennie --cov-report=html

# 린트 + 포맷 (CI 필수)
uv run ruff check .
uv run ruff format
```

---

## 📊 모니터링

### Grafana 대시보드

- URL: `http://localhost:3300`
- 기본 계정: admin / admin

### 로그 조회 (Loki)

```bash
# 특정 서비스 로그
docker compose logs price-monitor --tail 50

# Grafana에서 Loki 쿼리
{container_name="price-monitor"} |= "ERROR"
```

---

## 📝 라이선스

MIT License

---

<div align="center">

**Prime Jennie v2.0**

*AI가 발굴하고, 통계가 검증하고, 사람이 결정한다.*

</div>
