# Prime Jennie

AI-powered Korean stock trading system for KOSPI/KOSDAQ markets.

## Architecture

```
                        Airflow Scheduler
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
                    (KIS REST API proxy)
```

## Services

| Service | Port | Description |
|---------|------|-------------|
| kis-gateway | 8080 | KIS Securities API gateway |
| buy-scanner | 8081 | Real-time buy signal detection |
| buy-executor | 8082 | Buy order execution |
| sell-executor | 8083 | Sell order execution |
| daily-briefing | 8086 | Daily report generation |
| scout-job | 8087 | AI stock scoring pipeline |
| price-monitor | 8088 | Position monitoring + trailing stop |
| macro-council | 8089 | 3-expert macro analysis |
| dashboard | 8090 | REST API for dashboard |
| telegram | 8091 | Telegram command handler |
| dashboard-frontend | 80 | React dashboard UI |

## Tech Stack

**Backend**: Python 3.12, FastAPI, SQLModel, Redis, MariaDB
**Frontend**: React 18, TypeScript, Vite, Tailwind CSS, Recharts
**LLM**: vLLM (EXAONE 4.0 32B), DeepSeek Cloud (failover), Claude/Gemini
**Infra**: Docker Compose, Airflow, Qdrant (vector DB), Grafana/Loki

## Quick Start

```bash
# 1. Infrastructure
docker compose --profile infra up -d

# 2. Application services
docker compose --profile real up -d --build

# 3. Development
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/ -v
```

## Project Structure

```
prime_jennie/
├── domain/           # Domain models (Pydantic v2)
│   ├── enums.py     # MarketRegime, SectorGroup, SignalType, ...
│   ├── stock.py     # StockMaster, StockSnapshot, DailyPrice
│   ├── portfolio.py # Position, PortfolioState, DailySnapshot
│   ├── scoring.py   # HybridScore, QuantScore
│   ├── macro.py     # MacroInsight, TradingContext
│   ├── watchlist.py # HotWatchlist, WatchlistEntry
│   ├── signals.py   # BuySignal, SellOrder
│   └── config.py    # AppConfig (Pydantic Settings)
├── infra/            # Infrastructure adapters
│   ├── database/    # SQLModel ORM, repositories
│   ├── redis/       # TypedCache, RedisStreams
│   ├── llm/         # LLM providers (Ollama, OpenAI, Claude, Gemini)
│   └── kis/         # KIS API client
├── services/         # Microservices (FastAPI apps)
│   ├── gateway/     # KIS gateway proxy
│   ├── scout/       # AI scoring pipeline
│   ├── scanner/     # Buy signal detection
│   ├── buyer/       # Buy execution
│   ├── seller/      # Sell execution
│   ├── monitor/     # Price monitoring
│   ├── council/     # Macro council (3-expert)
│   ├── dashboard/   # Dashboard REST API
│   ├── briefing/    # Daily report
│   └── telegram/    # Telegram bot
├── prompts/          # LLM prompt templates
├── scripts/          # Utility scripts (data collection, etc.)
├── dags/             # Airflow DAGs
└── tests/            # Unit, E2E tests
```

## Key Features

- **Quant Scorer v2**: Potential-based scoring (momentum, quality, value, technical, news, supply-demand)
- **Unified Analyst**: Single LLM call with ±15pt guardrail + code-based risk_tag
- **Dynamic Sector Budget**: HOT/WARM/COOL tier allocation based on sector momentum
- **Macro Council**: 3-expert (Strategist → Risk Analyst → Chief Judge) structured analysis
- **Portfolio Guard**: Sector concentration limit + regime-based cash floor
- **Conviction Entry**: Early entry for high-confidence watchlist stocks (09:15-10:30)

## Testing

```bash
pytest tests/unit/ -v        # Unit tests
pytest tests/e2e/ -v         # End-to-end tests
pytest tests/ -v --tb=short  # All tests
```
