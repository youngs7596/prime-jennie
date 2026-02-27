#!/usr/bin/env bash
# ================================================================
# prime-jennie Installation Script
# ================================================================
# Usage:
#   curl -sSL <repo-url>/scripts/install.sh | bash
#   OR
#   git clone <repo-url> && cd prime-jennie && bash scripts/install.sh
# ================================================================
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

info()  { echo -e "${BLUE}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# ─── Pre-flight Checks ──────────────────────────────────────────
info "Checking prerequisites..."

check_cmd() {
    if ! command -v "$1" &>/dev/null; then
        error "$1 is required but not installed."
        echo "  Install: $2"
        return 1
    fi
    ok "$1 found: $(command -v "$1")"
}

MISSING=0
check_cmd python3 "sudo apt install python3" || MISSING=1
check_cmd pip3 "sudo apt install python3-pip" || MISSING=1
check_cmd docker "https://docs.docker.com/engine/install/" || MISSING=1
check_cmd docker-compose "pip install docker-compose" 2>/dev/null || \
    docker compose version &>/dev/null || MISSING=1

if [ "$MISSING" -eq 1 ]; then
    error "Missing prerequisites. Install them and re-run."
    exit 1
fi

# Python version check
PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
if [ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 12 ]); then
    error "Python 3.12+ required, found $PY_VERSION"
    exit 1
fi
ok "Python $PY_VERSION"

# ─── Step 1: Virtual Environment ────────────────────────────────
info "Setting up Python virtual environment..."

if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    ok "Created .venv"
else
    ok ".venv already exists"
fi

source .venv/bin/activate
pip install --upgrade pip -q
pip install -e ".[dev]" -q
ok "Python dependencies installed"

# ─── Step 2: Environment Configuration ──────────────────────────
if [ ! -f ".env" ]; then
    info "Creating .env from template..."
    cp .env.example .env

    # Auto-generate Airflow secrets
    FERNET_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" 2>/dev/null || openssl rand -base64 32)
    SECRET_KEY=$(openssl rand -base64 16)
    JWT_SECRET=$(openssl rand -base64 32)
    sed -i "s|^AIRFLOW_FERNET_KEY=.*|AIRFLOW_FERNET_KEY=${FERNET_KEY}|" .env
    sed -i "s|^AIRFLOW_SECRET_KEY=.*|AIRFLOW_SECRET_KEY=${SECRET_KEY}|" .env
    sed -i "s|^AIRFLOW_JWT_SECRET=.*|AIRFLOW_JWT_SECRET=${JWT_SECRET}|" .env
    ok "Airflow secrets auto-generated"

    warn ".env created — edit it with your API keys and database credentials"
    warn "  Required: DB_PASSWORD, KIS_APP_KEY, KIS_APP_SECRET, KIS_ACCOUNT_NO"
    warn "  Required: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_IDS"
    warn "  Required: At least one LLM API key (OPENROUTER_API_KEY or equivalent)"
else
    ok ".env already exists"
fi

# ─── Step 3: Docker Infrastructure ──────────────────────────────
info "Checking Docker..."

if ! docker info &>/dev/null; then
    warn "Docker daemon not running. Start it and run:"
    warn "  docker compose --profile infra up -d"
else
    ok "Docker daemon running"

    echo ""
    info "Ready to start infrastructure services?"
    info "This will start: MariaDB, Redis, Qdrant"
    read -p "Start infrastructure now? [y/N] " -n 1 -r
    echo ""

    if [[ $REPLY =~ ^[Yy]$ ]]; then
        info "Starting infrastructure..."
        docker compose --profile infra up -d
        ok "Infrastructure services started"

        # GPU check
        if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null; then
            read -p "NVIDIA GPU detected. Start vLLM (local LLM)? [y/N] " -n 1 -r
            echo ""
            if [[ $REPLY =~ ^[Yy]$ ]]; then
                docker compose --profile gpu up -d
                ok "vLLM services started"
            fi
        else
            warn "No NVIDIA GPU detected — vLLM skipped (Cloud LLM mode)"
            warn "Use: docker compose -f docker-compose.yml -f docker-compose.no-gpu.yml --profile infra --profile real up -d"
        fi

        info "Waiting for MariaDB to be ready..."
        for i in $(seq 1 30); do
            if docker compose exec -T mariadb mariadb -u root -e "SELECT 1" &>/dev/null 2>&1; then
                ok "MariaDB ready"
                break
            fi
            sleep 2
            if [ "$i" -eq 30 ]; then
                warn "MariaDB not ready after 60s — check logs: docker compose logs mariadb"
            fi
        done
    else
        info "Skipped. Start manually: docker compose --profile infra up -d"
    fi
fi

# ─── Step 4: Database Migration ─────────────────────────────────
info "Database setup..."
if [ -f "alembic.ini" ]; then
    echo ""
    read -p "Run database migrations? [y/N] " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        .venv/bin/alembic upgrade head
        ok "Database migrations applied"
    fi
else
    info "No alembic.ini found — migrations will be set up later"
fi

# ─── Step 5: Seed Stock Masters ───────────────────────────────
echo ""
read -p "Seed stock_masters table? (required for first install) [y/N] " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    info "Seeding stock_masters (KOSPI)... this takes ~60 seconds"
    .venv/bin/python scripts/seed_stock_masters.py --market KOSPI
    ok "Stock masters seeded"
else
    info "Skipped. Seed manually: python scripts/seed_stock_masters.py"
fi

# ─── Step 6: Verify Installation ────────────────────────────────
info "Verifying installation..."

.venv/bin/python3 -c "
from prime_jennie.domain import StockMaster, BuySignal, MarketRegime
from prime_jennie.domain.config import get_config
config = get_config()
print(f'  Config loaded: env={config.env}, db={config.db.host}:{config.db.port}')
print(f'  Domain models: OK')
" && ok "Package verification passed" || error "Package verification failed"

# ─── Done ────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════"
echo -e "${GREEN}  prime-jennie installation complete!${NC}"
echo "════════════════════════════════════════════════════════"
echo ""
echo "Next steps:"
echo "  1. Edit .env with your API keys and credentials"
echo "  2. Start infrastructure:  docker compose --profile infra up -d"
echo "  3. Start vLLM (GPU only): docker compose --profile gpu up -d"
echo "  4. Start trading services: docker compose --profile real up -d"
echo "     (No GPU? Use: docker compose -f docker-compose.yml -f docker-compose.no-gpu.yml --profile infra --profile real up -d)"
echo "  5. Run tests:             make test"
echo "  6. View dashboard:        http://localhost:80"
echo ""
