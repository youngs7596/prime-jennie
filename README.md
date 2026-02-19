# Prime Jennie

AI-powered Korean stock trading system for KOSPI/KOSDAQ markets.

## Architecture

```
                        Airflow Scheduler
            (scout, macro, data collection, monitor)
                              |
         +--------------------+--------------------+
         v                    v                    v
    scout-job            buy-scanner          price-monitor
   (AI scoring)        (entry signals)       (real-time watch)
         |                    |                    |
         +--------------------+--------------------+
                              v
                    buy-executor / sell-executor
                         (order execution)
                              |
                              v
                        kis-gateway
                (KIS REST API + WebSocket proxy)
                              |
         +--------------------+--------------------+
         v                    v                    v
    macro-council        news-pipeline         dashboard
   (3-expert LLM)      (crawl + analyze)     (API + React UI)
```

## Services

### Trading Services (profile: real)

| Service | Port | Description |
|---------|------|-------------|
| kis-gateway | 8080 | KIS Securities API gateway + WebSocket streamer |
| buy-scanner | 8081 | Real-time buy signal detection (tick consumer) |
| buy-executor | 8082 | Buy order execution + Portfolio Guard |
| sell-executor | 8083 | Sell order execution (trailing stop, time exit) |
| daily-briefing | 8086 | Daily report generation + Telegram 발송 |
| scout-job | 8087 | AI stock scoring pipeline (Quant v2 + Unified Analyst) |
| price-monitor | 8088 | Position monitoring + trailing stop + stop loss |
| macro-council | 8089 | 3-expert macro analysis (Strategist → Risk → Judge) |
| dashboard | 8090 | REST API (portfolio, watchlist, macro, trades, LLM stats) |
| telegram | 8091 | Telegram command handler (polling) |
| news-pipeline | 8092 | News crawl → LLM sentiment → Qdrant archiving |
| job-worker | 8095 | Utility jobs for Airflow DAGs (data collection, cleanup) |
| dashboard-frontend | 80 | React dashboard UI (Nginx reverse proxy) |

### Infrastructure Services (profile: infra)

| Service | Port | Description |
|---------|------|-------------|
| mariadb | 3307 | Persistent storage (SQLModel ORM) |
| redis | 6379 | Cache, streams, state, messaging |
| qdrant | 6333 | Vector DB for news RAG |
| vllm-llm | 8001 | EXAONE 4.0 32B AWQ (local LLM) |
| vllm-embed | 8002 | KURE-v1 (embedding model) |
| grafana | 3300 | Monitoring dashboard |
| loki | 3100 | Log aggregation |

## Tech Stack

- **Backend**: Python 3.12, FastAPI, Pydantic v2, SQLModel, Redis Streams
- **Frontend**: React 18, TypeScript, Vite, Tailwind CSS, Recharts, TanStack Query
- **Database**: MariaDB (ORM: SQLModel/SQLAlchemy), Redis (cache + messaging)
- **LLM**: vLLM (EXAONE 4.0 32B AWQ), DeepSeek Cloud (failover), Claude, Gemini
- **Vector DB**: Qdrant + KURE-v1 embedding
- **Infra**: Docker Compose, Airflow (DAG scheduler), GitHub Actions CI/CD
- **Observability**: Grafana + Loki (structured logging)

## Quick Start

```bash
# 1. Clone
git clone https://github.com/youngs7596/prime-jennie.git
cd prime-jennie

# 2. Development environment
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 3. Run tests
pytest tests/ -v

# 4. Infrastructure (Docker)
docker compose --profile infra up -d

# 5. Application services (Docker)
docker compose --profile real up -d --build
```

## Project Structure

```
prime-jennie/
├── prime_jennie/
│   ├── domain/           # 도메인 모델 (30+ Pydantic v2 models)
│   │   ├── enums.py     # MarketRegime, SectorGroup, SignalType, TradeTier
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
│   │   ├── llm/         # Provider factory (Ollama, OpenAI, Claude, Gemini, CloudFailover)
│   │   ├── kis/         # KIS API client (Gateway proxy)
│   │   ├── crawlers/    # Naver sector crawler
│   │   └── observability/ # Structured logging, LLM usage metrics
│   └── services/         # 마이크로서비스 (FastAPI apps)
│       ├── base.py      # App factory (create_app) + common /health
│       ├── deps.py      # FastAPI Depends (Redis, DB session, KIS client)
│       ├── gateway/     # KIS REST API proxy + WebSocket streamer
│       ├── scout/       # AI scoring (universe → enrich → quant → analyst → select)
│       ├── scanner/     # Real-time buy signal (tick consumer + strategies)
│       ├── buyer/       # Buy execution (Portfolio Guard, position sizing)
│       ├── seller/      # Sell execution (trailing stop, RSI, time exit)
│       ├── monitor/     # Price monitoring loop
│       ├── council/     # Macro council (3-expert structured JSON)
│       ├── news/        # News pipeline (crawl → analyze → archive)
│       ├── dashboard/   # Dashboard REST API (6 routers)
│       ├── briefing/    # Daily report + Telegram send
│       ├── telegram/    # Telegram bot (polling + command handler)
│       └── jobs/        # Airflow utility jobs (data collection, cleanup)
├── frontend/             # React 18 + TypeScript + Vite + Tailwind
├── dags/                 # Airflow DAGs (scout, macro, utility, monitor)
├── prompts/              # LLM prompt templates (council, analyst, news, briefing)
├── scripts/              # Utility scripts
├── infra/                # Loki/Grafana config
├── tests/                # Unit (446), E2E, integration
├── .ai/                  # AI assistant rules + session handoffs
├── .github/workflows/    # CI/CD (lint, test, deploy)
├── docker-compose.yml    # 22 services (infra + real profiles)
└── pyproject.toml        # Dependencies + tool config
```

## Key Features

- **Quant Scorer v2**: 잠재력 기반 스코어링 (모멘텀20 + 품질20 + 가치20 + 기술10 + 뉴스10 + 수급20)
- **Unified Analyst**: 1-pass LLM 호출 + ±15pt 가드레일 + 코드 기반 risk_tag
- **Dynamic Sector Budget**: HOT/WARM/COOL 티어 → 섹터별 종목 수 cap 자동 배정
- **Macro Council**: 전략가 → 리스크분석가 → 수석심판 (구조화 JSON 파이프라인)
- **Portfolio Guard**: 동적 섹터 cap + 국면별 현금 하한선 (BULL 10%, BEAR 25%)
- **Conviction Entry**: 고확신 종목 장 초반 선제 진입 (09:15-10:30)
- **KIS WebSocket**: 실시간 체결가 → Redis Stream → Scanner tick consumer
- **LLM Usage Stats**: 서비스별 호출/토큰 자동 기록 → Dashboard 표시

## Data Flow

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

## Testing

```bash
# All tests (446 passed)
pytest tests/ -v --tb=short

# Unit tests only
pytest tests/unit/ -v

# E2E tests only
pytest tests/e2e/ -v

# Specific service
pytest tests/unit/services/test_gateway.py -v

# Coverage
pytest tests/ --cov=prime_jennie --cov-report=html
```

## Configuration

환경변수 기반 설정 (Pydantic Settings, env prefix 자동 매핑):

| Prefix | Config Class | Example |
|--------|-------------|---------|
| `DB_` | DatabaseConfig | `DB_HOST`, `DB_PORT`, `DB_NAME` |
| `REDIS_` | RedisConfig | `REDIS_HOST`, `REDIS_PORT` |
| `KIS_` | KISConfig | `KIS_APP_KEY`, `KIS_GATEWAY_URL` |
| `LLM_` | LLMConfig | `LLM_TIER_FAST_PROVIDER`, `LLM_VLLM_LLM_URL` |
| `RISK_` | RiskConfig | `RISK_MAX_PORTFOLIO_SIZE`, `RISK_MAX_BUY_COUNT_PER_DAY` |
| `SCORING_` | ScoringConfig | `SCORING_QUANT_SCORER_VERSION` |
| `SCANNER_` | ScannerConfig | `SCANNER_CONVICTION_ENTRY_ENABLED` |
| `SELL_` | SellConfig | `SELL_TRAILING_ENABLED`, `SELL_STOP_LOSS_PCT` |
| `INFRA_` | InfraConfig | `INFRA_QDRANT_URL` |

## License

MIT
