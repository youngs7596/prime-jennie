# Prime Jennie — AI 기반 한국 주식 자율 트레이딩 시스템

<div align="center">

![Version](https://img.shields.io/badge/version-2.3.0-blue)
![Python](https://img.shields.io/badge/python-3.12-green)
![Docker](https://img.shields.io/badge/docker-compose-2496ED)
![Airflow](https://img.shields.io/badge/airflow-2.10-017CEE)
![Tests](https://img.shields.io/badge/tests-596%20passed-brightgreen)
![License](https://img.shields.io/badge/license-MIT-yellow)

**멀티 LLM 기반 한국 주식 자율 트레이딩 시스템**

*"AI가 발굴하고, 통계가 검증하고, 사람이 결정한다."*

</div>

---

## 목차

- [개요](#개요)
- [빠른 시작](#빠른-시작)
- [GPU 모드 vs Cloud 모드](#gpu-모드-vs-cloud-모드)
- [핵심 기능](#핵심-기능)
- [시스템 아키텍처](#시스템-아키텍처)
- [서비스 구성](#서비스-구성)
- [기술 스택](#기술-스택)
- [프로젝트 구조](#프로젝트-구조)
- [데이터 흐름](#데이터-흐름)
- [Exit Rules 체계](#exit-rules-체계)
- [리스크 관리](#리스크-관리)
- [설정](#설정)
- [테스트](#테스트)
- [모니터링](#모니터링)

---

## 개요

**Prime Jennie**는 한국투자증권 Open API를 활용한 AI 기반 자율 트레이딩 에이전트입니다.

### 주요 특징

| 기능 | 설명 |
|------|------|
| **하이브리드 스코어링** | Quant Scorer v2.3(정량 60%) + Unified Analyst(LLM 정성 40%), ±15pt 가드레일 + Forward 컨센서스 |
| **Macro Council** | 전략가 → 리스크분석가 → 수석심판 (3인 구조화 JSON, sentiment_score 기반 국면 판정) |
| **12단계 Exit Rules v2** | Hard Stop → Profit Lock → Breakeven Stop → ATR → Trailing TP → Scale-Out 등 우선순위 체인 |
| **KIS WebSocket** | 실시간 체결가 → Redis Stream → Scanner tick consumer |
| **텔레그램 알림** | 매수/매도 체결 실시간 알림 (Redis Stream 비동기 발송) |
| **Portfolio Guard** | 동적 섹터 cap(30%) + 종목 cap(15%) + 국면별 현금 하한선 (BULL 10%, BEAR 25%) |
| **리스크 관리** | Correlation check(0.85) + Cooldown(손절 3일/매도 24h) + 섹터 비중 제한 |
| **국면 연동** | BULL/SIDEWAYS/BEAR 국면별 차등 전략 (스톱, 익절, 타임아웃, 매수 제한) |
| **GPU-Free 지원** | GPU 없이도 Cloud LLM(DeepSeek)으로 전체 시스템 구동 가능 |

---

## 빠른 시작

### 사전 요구사항

| 필수 | 선택 |
|------|------|
| Docker & Docker Compose v2 | NVIDIA GPU (RTX 3090/4090 권장, 로컬 LLM용) |
| Python 3.12+ | uv (Python 패키지 매니저) |
| [한국투자증권 Open API](https://apiportal.koreainvestment.com) 발급 | Cloudflare Tunnel (외부 접근) |
| [Telegram Bot](https://core.telegram.org/bots#creating-a-new-bot) 토큰 | |

### Step 1. 클론 및 설치

```bash
git clone https://github.com/youngs7596/prime-jennie.git
cd prime-jennie

# 자동 설치 (venv 생성, 의존성 설치, .env 생성, Airflow 시크릿 자동 생성)
bash scripts/install.sh
```

`install.sh`가 하는 일:
1. Python venv 생성 + 의존성 설치
2. `.env.example` → `.env` 복사 + Airflow 시크릿 자동 생성
3. Docker 인프라 시작 (MariaDB, Redis, Qdrant 등)
4. GPU 감지 → vLLM 자동 시작 제안 (없으면 Cloud 모드 안내)
5. DB 마이그레이션 (Alembic)
6. **stock_masters 시딩** — pykrx로 KOSPI 전 종목 자동 수집

### Step 2. API 키 설정

`.env` 파일을 열어 필수 값을 입력합니다:

```bash
vi .env
```

**필수 설정:**
```env
# 한국투자증권 (https://apiportal.koreainvestment.com)
KIS_APP_KEY=your_app_key
KIS_APP_SECRET=your_app_secret
KIS_ACCOUNT_NO=12345678          # 계좌번호 8자리

# 텔레그램 알림
TELEGRAM_BOT_TOKEN=123456:ABC-DEF
TELEGRAM_CHAT_IDS=987654321      # 콤마 구분 복수 가능

# LLM (최소 하나 필요)
OPENROUTER_API_KEY=sk-or-...     # DeepSeek Cloud 사용 (가장 저렴)
```

**선택 설정:**
```env
DB_PASSWORD=your_db_password     # 기본값: 비어있음 (보안상 변경 권장)
OPENAI_API_KEY=sk-...            # Cloud 모드 임베딩 시 필요
DOCKER_DATA_DIR=/docker_data     # Docker 볼륨 경로 (기본: /docker_data)
```

### Step 3. 서비스 실행

**GPU 모드** (NVIDIA GPU 보유):
```bash
# 인프라 + vLLM
docker compose --profile infra --profile gpu up -d

# 트레이딩 서비스
docker compose --profile real up -d --build
```

**Cloud 모드** (GPU 없음):
```bash
# 인프라 (vLLM 제외)
docker compose --profile infra up -d

# 트레이딩 서비스 (Cloud LLM 오버라이드)
docker compose -f docker-compose.yml -f docker-compose.no-gpu.yml \
  --profile infra --profile real up -d --build
```

### Step 4. 확인

```bash
# 서비스 상태 확인
docker compose --profile infra --profile real ps

# 대시보드 접속
open http://localhost:80

# 로그 확인
docker compose logs scout-job --tail 20
```

### 수동 설치 (install.sh 없이)

```bash
# 1. venv + 의존성
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 2. 환경 설정
cp .env.example .env
# .env 편집 (API 키, DB 비밀번호, Airflow 시크릿 직접 생성)

# 3. 인프라
docker compose --profile infra up -d

# 4. DB 마이그레이션
alembic upgrade head

# 5. 종목 마스터 시딩 (필수! 없으면 Scout 실패)
python scripts/seed_stock_masters.py --market KOSPI

# 6. 트레이딩 서비스
docker compose --profile real up -d --build
```

---

## GPU 모드 vs Cloud 모드

| | GPU 모드 | Cloud 모드 |
|---|----------|-----------|
| **하드웨어** | NVIDIA GPU (VRAM 24GB+) | CPU only |
| **로컬 LLM** | vLLM (EXAONE 4.0 32B AWQ) | 미사용 |
| **FAST tier** | 로컬 vLLM (무료) | DeepSeek Cloud ($0.14/M input) |
| **REASONING tier** | DeepSeek Cloud | DeepSeek Cloud (동일) |
| **임베딩** | KURE-v1 (로컬 vLLM) | OpenAI Embeddings |
| **뉴스 RAG** | Qdrant + 로컬 임베딩 | 비활성화 |
| **필수 API 키** | OPENROUTER_API_KEY | OPENROUTER_API_KEY + OPENAI_API_KEY |
| **실행 방법** | `--profile gpu` 추가 | `docker-compose.no-gpu.yml` 오버라이드 |

Cloud 모드에서는 뉴스 RAG가 비활성화되지만, Scout의 핵심 기능(Quant Scoring + LLM Analyst)은 모두 동작합니다.

---

## 핵심 기능

### 1. Scout Pipeline (종목 발굴)

```
KOSPI Universe (~123종목, 시총 500억 이상)
       ↓
[Phase 1] Quant Scoring v2.3 (잠재력 기반)
   - 모멘텀20 + 품질20 + 가치20 + 기술10 + 뉴스10 + 수급14(외인6+기관8) + 섹터모멘텀10 = 100+
   - Forward 컨센서스: FnGuide PER/ROE → quality/value 서브팩터 가산
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
[Score Smoothing] MA(window=3) 이동평균 + 히스테리시스
   - Entry: MA score >= 62 → Watchlist 진입
   - Exit: MA score < 55 → Watchlist 제거
   - 유지구간(55~62): 기존 WL 종목만 유지
       ↓
Watchlist (최대 25개, 시총 타이브레이커)
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

### 3. Exit Rules v2 (12단계 우선순위 체인)

```
Hard Stop(-10%) → Profit Floor → Profit Lock(ATR) → Breakeven Stop(+3%→+0.3%)
    → ATR Stop → Fixed Stop(-6%) → Trailing TP → Scale-Out
    → RSI Overbought(Trailing 활성 시 스킵) → Target Price
    → Death Cross(BEAR/SIDEWAYS 전용) → Time Exit
```

- **Breakeven Stop**: +3% 도달 후 +0.3% 미만 시 전량 매도
- **Scale-Out**: 국면별 분할 익절 (BULL 3단계, SIDEWAYS/BEAR 4단계)
- **Time-Tightening**: 장기 보유 시 손절선 점진 축소 (BULL 15일, SIDEWAYS/BEAR 10일 시작)
- **RSI v2**: Trailing TP 활성 시 RSI 규칙 스킵 (조기 익절 방지)
- **Death Cross v2**: BULL/STRONG_BULL 국면에서 비활성화

### 4. Macro Council (3인 전문가 회의)

```
[매크로 데이터 수집] → [Strategist 전략가] → [Risk Analyst 리스크분석가] → [Judge 수석심판]
                                                                              ↓
                                                                 TradingContext (Redis)
                                                                 - 시장 국면 (sentiment_score 기반)
                                                                   >=70 STRONG_BULL, >=55 BULL,
                                                                   >=40 SIDEWAYS, >=25 BEAR, <25 STRONG_BEAR
                                                                 - 섹터 HOT/WARM/COOL
                                                                 - 현금 비중 권고
```

---

## 시스템 아키텍처

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          Prime Jennie System                            │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌───────────────┐    ┌───────────────┐    ┌───────────────┐           │
│  │ News Pipeline │───>│    Qdrant     │<───│  Scout Job    │           │
│  │ (Crawl+Analyze)    │   (RAG)       │    │ (Quant+LLM)  │           │
│  └───────────────┘    └───────────────┘    └───────────────┘           │
│         │                                          │                    │
│         v                                          v                    │
│  ┌───────────────┐    ┌───────────────┐    ┌───────────────┐           │
│  │    Redis      │<───│  KIS Gateway  │───>│  Buy Scanner  │           │
│  │(Cache+Stream) │    │ (REST+WS)     │    │ (Tick Consumer)│           │
│  └───────────────┘    └───────────────┘    └───────────────┘           │
│         │                    │                     │                    │
│         v                    v                     v                    │
│  ┌───────────────┐    ┌───────────────┐    ┌───────────────┐           │
│  │   MariaDB     │<───│ Price Monitor │───>│ Buy Executor  │           │
│  │  (SQLModel)   │    │ (Exit Rules)  │    │(Portfolio Guard)│          │
│  └───────────────┘    └───────────────┘    └───────────────┘           │
│                              │                                          │
│                              v                                          │
│  ┌───────────────┐    ┌───────────────┐    ┌───────────────┐           │
│  │  Job Worker   │    │ Sell Executor │    │ Macro Council │           │
│  │(크롤러+정기작업)│    │(Scale-Out/Stop)│    │(3인 전문가)   │           │
│  └───────────────┘    └───────────────┘    └───────────────┘           │
│                                                                         │
├─────────────────────────────────────────────────────────────────────────┤
│  Dashboard (React + FastAPI)  │  Grafana + Loki  │  Telegram Bot       │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 서비스 구성

### Trading Services (profile: real)

| 서비스 | 포트 | 설명 |
|--------|------|------|
| **kis-gateway** | 8080 | KIS Securities API 게이트웨이 + WebSocket streamer |
| **buy-scanner** | 8081 | 실시간 매수 신호 탐지 (tick consumer, regime 연동) |
| **buy-executor** | 8082 | 매수 주문 실행 + Portfolio Guard + Correlation check |
| **sell-executor** | 8083 | 매도 주문 실행 (trailing stop, scale-out) |
| **daily-briefing** | 8086 | 일간 리포트 생성 + Telegram 발송 |
| **scout-job** | 8087 | AI 종목 발굴 (Quant v2.3 + Unified Analyst + MA smoothing) |
| **price-monitor** | 8088 | 포지션 모니터링 + 12단계 Exit Rules v2 |
| **macro-council** | 8089 | 3인 전문가 매크로 분석 (sentiment_score 기반 국면) |
| **dashboard** | 8090 | REST API (portfolio, watchlist, macro, trades, LLM stats) |
| **telegram** | 8091 | Telegram 명령 핸들러 (polling) |
| **news-pipeline** | 8092 | 뉴스 크롤 → LLM 감성 분석 → Qdrant 저장 |
| **job-worker** | 8095 | 크롤러 + 정기 데이터 수집/정리 (Airflow DAG 연동) |
| **dashboard-frontend** | 80 | React 대시보드 UI (Nginx reverse proxy) |

### Infrastructure Services (profile: infra)

| 서비스 | 포트 | 설명 |
|--------|------|------|
| **mariadb** | 3307 | 영구 저장소 (SQLModel ORM) |
| **redis** | 6379 | 캐시, 스트림, 상태, 메시징 |
| **qdrant** | 6333 | 벡터 DB (뉴스 RAG) |
| **grafana** | 3300 | 모니터링 대시보드 |
| **loki** | 3100 | 로그 집계 |
| **promtail** | - | 로그 수집 에이전트 |
| **cloudflared** | - | Cloudflare Tunnel (외부 접근) |
| **airflow-webserver** | 8085 | Airflow UI |
| **airflow-scheduler** | - | DAG 스케줄러 |

### GPU Services (profile: gpu)

| 서비스 | 포트 | 설명 |
|--------|------|------|
| **vllm-llm** | 8001 | EXAONE 4.0 32B AWQ (로컬 LLM 추론, NVIDIA GPU 필수) |
| **vllm-embed** | 8002 | KURE-v1 (한국어 임베딩 모델, NVIDIA GPU 필수) |

### 자동화 작업 (Airflow DAGs)

| DAG | 시간 (KST) | 설명 |
|-----|------------|------|
| **scout_pipeline** | 평일 08:30-14:30, 1시간 | AI 종목 발굴 |
| **macro_collection** | 평일 07:40, 11:40 | 글로벌 매크로 수집 |
| **macro_council** | 평일 07:50, 11:50 | 3인 매크로 분석 |
| **macro_quick** | 평일 09:30-14:30, 1시간 | 장중 매크로 빠른 업데이트 |
| **price_monitor_ops** | 평일 09:00/15:30 | 가격 모니터 시작/중지 |
| **daily_briefing** | 평일 17:00 | 브리핑 Telegram 발송 |
| **daily_asset_snapshot** | 평일 15:45 | 일일 자산 스냅샷 |
| **data_collection** | 평일 16:00-18:45 | 일봉, 수급, DART 공시 수집 |
| **collect_consensus** | 월/목 06:00 | FnGuide/Naver 컨센서스 수집 |
| **collect_quarterly_financials** | 1/4/7/10월 15일 04:00 | 분기 재무(PER/PBR/ROE) 갱신 |
| **collect_monthly_roe** | 매월 1일 03:00 | 월간 ROE 수집 |
| **contract_smoke_test** | 매일 21:00 | 외부 크롤러 5개 계약 검증 (실패 시 텔레그램 알림) |
| **data_cleanup_weekly** | 일 03:00 | 오래된 데이터 정리 |

---

## 기술 스택

### 백엔드
- **Python 3.12** — 핵심 언어
- **FastAPI** — REST API (Pydantic v2 자동 검증)
- **SQLModel** — ORM (SQLAlchemy 2.0 + Pydantic v2)
- **Redis Streams** — 서비스 간 비동기 메시징

### AI / ML
- **vLLM v0.15.1** — 로컬 LLM 추론 (EXAONE 4.0 32B AWQ) — GPU 모드
- **KURE-v1** — 한국어 임베딩 모델 — GPU 모드
- **DeepSeek Cloud** — REASONING/THINKING 티어 (failover)
- **OpenAI Embeddings** — Cloud 모드 임베딩
- **Qdrant** — 벡터 저장소 (뉴스 RAG)

### 데이터
- **MariaDB** — 영구 저장소 (SQLModel ORM, Alembic 마이그레이션)
- **Redis** — 캐시, 실시간 상태, 스트림 메시징
- **Naver Finance / FnGuide** — 재무 데이터, 컨센서스 크롤링
- **pykrx** — KRX 시세, 시가총액, 수급 데이터

### 프론트엔드
- **React 18 + TypeScript** — Dashboard UI
- **Vite** — 빌드 도구
- **Tailwind CSS** — 스타일링
- **Recharts + TanStack Query** — 차트, 데이터 페칭

### 인프라
- **Docker Compose** — 24개 서비스 (infra + gpu + real 프로파일)
- **Airflow** — 15+ DAG 기반 워크플로우 스케줄러
- **GitHub Actions** — CI/CD (lint + test + deploy)
- **Grafana + Loki** — 모니터링 + 로그 집계

---

## 프로젝트 구조

```
prime-jennie/
├── prime_jennie/
│   ├── domain/           # 도메인 모델 (30+ Pydantic v2 models)
│   │   ├── enums.py     # MarketRegime, SectorGroup(15개), SignalType, SellReason
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
│   │   ├── llm/         # Provider factory (vLLM, DeepSeek, Claude, Gemini, OpenAI)
│   │   ├── kis/         # KIS API client (Gateway proxy)
│   │   ├── crawlers/    # Naver(재무/ROE/뉴스/섹터) + FnGuide(컨센서스)
│   │   └── observability/ # Structured logging, LLM usage metrics
│   └── services/         # 마이크로서비스 (FastAPI apps)
│       ├── base.py      # App factory (create_app) + common /health
│       ├── deps.py      # FastAPI Depends (Redis, DB session, KIS client)
│       ├── gateway/     # KIS REST API proxy + WebSocket streamer
│       ├── scout/       # AI scoring pipeline (Quant v2.3 + MA smoothing)
│       ├── scanner/     # Real-time buy signal (tick consumer + strategies)
│       ├── buyer/       # Buy execution (Portfolio Guard, correlation check)
│       ├── seller/      # Sell execution
│       ├── monitor/     # Price monitoring + 12-rule Exit Rules v2
│       ├── council/     # Macro council (3-expert, sentiment_score 기반)
│       ├── news/        # News pipeline (crawl → analyze → archive)
│       ├── dashboard/   # Dashboard REST API (6 routers)
│       ├── briefing/    # Daily report + Telegram send
│       ├── telegram/    # Telegram bot (polling + command handler)
│       └── jobs/        # 크롤러 + 정기작업 (Airflow DAG 연동)
├── frontend/             # React 18 + TypeScript + Vite + Tailwind
├── dags/                 # Airflow DAGs (scout, macro, utility, monitor)
├── scripts/              # 설치, 시딩, 유틸리티 스크립트
│   ├── install.sh       # 자동 설치 스크립트
│   └── seed_stock_masters.py  # 종목 마스터 초기 시딩
├── migrations/           # Alembic DB migrations (version_table: alembic_version_app)
├── infra/                # Loki/Grafana config
├── tests/                # Unit + E2E + Contract (596 passed)
├── docker-compose.yml    # 메인 (infra + gpu + real 프로파일)
├── docker-compose.no-gpu.yml  # GPU-Free 오버라이드
└── pyproject.toml        # Dependencies + tool config (uv)
```

---

## 데이터 흐름

```
[Macro Collection] → [Macro Council] → TradingContext (Redis, sentiment_score 기반)
                                              ↓
[Scout Pipeline] KOSPI Universe → Enrich → Quant v2.3 → LLM Analyst
                                                              ↓
                                              MA Smoothing + 히스테리시스 → Watchlist (Redis)
                                                                                ↓
[KIS WebSocket] → Redis kis:prices → [Scanner] → BuySignal (Redis Stream)
                                                        ↓
                                              [Buy Executor] → KIS Gateway → 주문
                                                        ↓
                                              [Price Monitor] → [Sell Executor] → 매도
```

---

## Exit Rules 체계

12개 규칙이 우선순위 체인으로 평가됩니다. 첫 번째 매칭 규칙이 실행됩니다.

| 순위 | 규칙 | 조건 | 매도 비율 | 비고 |
|------|------|------|----------|------|
| 1 | **Hard Stop** | profit <= -10% | 100% | |
| 2 | **Profit Floor** | 고점 대비 급락 (floor 활성) | 100% | |
| 3 | **Profit Lock** | ATR 기반 동적 이익 보호 (L1/L2) | 100% | |
| 4 | **Breakeven Stop** | +3% 도달 후 +0.3% 미만 | 100% | |
| 5 | **ATR Stop** | 매수가 - ATR x 2 이하 | 100% | |
| 6 | **Fixed Stop** | profit <= -6% (Time-Tightening 적용) | 100% | |
| 7 | **Trailing TP** | 고점 대비 -3.5% 하락 | 100% | |
| 8 | **Scale-Out** | 국면별 분할 익절 단계 | 15~25% | |
| 9 | **RSI Overbought** | RSI >= 75 & profit >= 3% | 50% | Trailing TP 활성 시 스킵 |
| 10 | **Target Price** | 목표가 도달 | 100% | |
| 11 | **Death Cross** | 데드크로스 & 손실 중 | 100% | BULL/STRONG_BULL 비활성 |
| 12 | **Time Exit** | 국면별 최대 보유일 초과 | 100% | |

### Scale-Out 단계 (국면별)

| 국면 | 단계 | 설명 |
|------|------|------|
| **BULL** | 3단계 | +7.0%(25%), +15.0%(25%), +25.0%(15%) |
| **SIDEWAYS** | 4단계 | +3.0%(25%), +7.0%(25%), +12.0%(25%), +18.0%(15%) |
| **BEAR** | 4단계 | +2.0%(25%), +5.0%(25%), +8.0%(25%), +12.0%(15%) |

---

## 리스크 관리

| 기능 | 설명 |
|------|------|
| **Correlation Check** | 보유 종목과 상관관계 0.85 이상 시 매수 차단 |
| **Cooldown** | 손절/데드크로스/브레이크이븐 후 3일 + 모든 매도 후 24h 재매수 방지 (Redis 기반) |
| **Portfolio Guard** | 섹터 금액 비중 30% (STRONG_BULL 50%) + 종목 금액 비중 15% (STRONG_BULL 25%) |
| **현금 하한선** | BULL 10%, SIDEWAYS 15%, BEAR 25% |
| **일일 매수 제한** | 국면별 최대 매수 건수 제한 |
| **Contract Smoke Test** | 외부 크롤러 5개 매일 21:00 검증, 실패 시 텔레그램 알림 |

---

## 설정

환경변수 기반 설정 (Pydantic Settings, env prefix 자동 매핑):

| Prefix | Config Class | 예시 |
|--------|-------------|------|
| `DB_` | DatabaseConfig | `DB_HOST`, `DB_PORT`, `DB_NAME` |
| `REDIS_` | RedisConfig | `REDIS_HOST`, `REDIS_PORT` |
| `KIS_` | KISConfig | `KIS_APP_KEY`, `KIS_GATEWAY_URL` |
| `LLM_` | LLMConfig | `LLM_TIER_FAST_PROVIDER`, `LLM_EMBED_MODEL`, `LLM_EMBED_PROVIDER` |
| `RISK_` | RiskConfig | `RISK_MAX_PORTFOLIO_SIZE`, `RISK_MAX_BUY_COUNT_PER_DAY` |
| `SCORING_` | ScoringConfig | `SCORING_QUANT_SCORER_VERSION` |
| `SCANNER_` | ScannerConfig | `SCANNER_CONVICTION_ENTRY_ENABLED` |
| `SELL_` | SellConfig | `SELL_TRAILING_ENABLED`, `SELL_STOP_LOSS_PCT` |
| `SCOUT_` | ScoutConfig | `SCOUT_MA_WINDOW`, `SCOUT_UNIVERSE_MARKET` |
| `INFRA_` | InfraConfig | `INFRA_QDRANT_URL` |

전체 설정 목록은 `.env.example`을 참고하세요.

### Docker Compose 프로파일

| 프로파일 | 목적 | 서비스 |
|----------|------|--------|
| `infra` | 인프라 서비스 | MariaDB, Redis, Qdrant, Grafana, Loki, Airflow |
| `gpu` | GPU 서비스 | vLLM-LLM, vLLM-Embed (NVIDIA GPU 필수) |
| `real` | 트레이딩 서비스 | 모든 트레이딩 + 대시보드 (infra 필요) |

```bash
# GPU 모드: 전체 시스템
docker compose --profile infra --profile gpu --profile real up -d

# Cloud 모드: GPU 없이
docker compose -f docker-compose.yml -f docker-compose.no-gpu.yml \
  --profile infra --profile real up -d

# 인프라만 (개발 시)
docker compose --profile infra up -d
```

---

## 테스트

```bash
# 전체 테스트 (596 passed)
uv run pytest tests/ -v --tb=short

# Unit 테스트만
uv run pytest tests/unit/ -v

# Contract 테스트 (외부 크롤러 검증)
uv run pytest tests/contract/ -v

# 특정 서비스
uv run pytest tests/unit/services/test_exit_rules.py -v

# 커버리지
uv run pytest tests/ --cov=prime_jennie --cov-report=html

# 린트 + 포맷 (CI 필수)
uv run ruff check .
uv run ruff format
```

---

## 모니터링

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

## 라이선스

MIT License

---

<div align="center">

**Prime Jennie v2.3**

*AI가 발굴하고, 통계가 검증하고, 사람이 결정한다.*

</div>
