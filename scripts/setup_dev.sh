#!/usr/bin/env bash
# ================================================================
# prime-jennie: Development Testbed Setup
# ================================================================
# MS-01(운영)의 인프라를 원격 연결하되, 거래 데이터는 분리
#
# 수행 내용:
#   1. MS-01에 jennie_db_dev DB 생성 + 권한 부여
#   2. alembic upgrade head → 전체 테이블 생성
#   3. 참조 테이블 14개를 VIEW로 교체 (운영 DB 참조)
#   4. Redis DB 1번에 안전 플래그 설정
#
# 사용법:
#   bash scripts/setup_dev.sh
#
# 전제 조건:
#   - MS-01 SSH 접속 가능 (ssh youngs75@192.168.31.195)
#   - .env.dev 파일 존재 (DB 비밀번호 등)
#   - uv + alembic 설치됨
# ================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$PROJECT_DIR/.env.dev"
SQL_FILE="$SCRIPT_DIR/setup_dev_views.sql"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()  { echo -e "${BLUE}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[ OK ]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERR ]${NC} $*" >&2; }

# ─── Load .env.dev ────────────────────────────────────────────
if [ ! -f "$ENV_FILE" ]; then
    error ".env.dev not found at $ENV_FILE"
    error "Copy from .env and modify DB_HOST, DB_NAME, REDIS_HOST, REDIS_DB"
    exit 1
fi

# Parse .env.dev (skip comments and empty lines)
set -a
# shellcheck disable=SC1090
source <(grep -v '^\s*#' "$ENV_FILE" | grep -v '^\s*$')
set +a

MS01_HOST="${DB_HOST:?DB_HOST not set in .env.dev}"
MS01_PORT="${DB_PORT:?DB_PORT not set in .env.dev}"
DB_USER="${DB_USER:?DB_USER not set in .env.dev}"
DB_PASS="${DB_PASSWORD:?DB_PASSWORD not set in .env.dev}"
DEV_DB="${DB_NAME:?DB_NAME not set in .env.dev}"
PROD_DB="jennie_db"

REDIS_HOST="${REDIS_HOST:?REDIS_HOST not set in .env.dev}"
REDIS_PORT="${REDIS_PORT:-6379}"
REDIS_DB_NUM="${REDIS_DB:-1}"
REDIS_PASS="${REDIS_PASSWORD:-}"

MARIADB_CMD="mariadb -h $MS01_HOST -P $MS01_PORT -u $DB_USER -p$DB_PASS"

echo ""
echo "════════════════════════════════════════════════════════"
echo -e "${BLUE}  prime-jennie Development Testbed Setup${NC}"
echo "════════════════════════════════════════════════════════"
echo ""
echo "  MS-01:     $MS01_HOST:$MS01_PORT"
echo "  Dev DB:    $DEV_DB"
echo "  Prod DB:   $PROD_DB (VIEW 참조)"
echo "  Redis:     $REDIS_HOST:$REDIS_PORT DB=$REDIS_DB_NUM"
echo ""

# ─── Step 0: Connectivity Check ──────────────────────────────
info "Checking MS-01 connectivity..."

if ! $MARIADB_CMD -e "SELECT 1" &>/dev/null; then
    error "Cannot connect to MariaDB at $MS01_HOST:$MS01_PORT"
    error "Check DB_HOST, DB_PORT, DB_USER, DB_PASSWORD in .env.dev"
    exit 1
fi
ok "MariaDB connection OK"

REDIS_AUTH=""
if [ -n "$REDIS_PASS" ]; then
    REDIS_AUTH="-a $REDIS_PASS"
fi

if ! redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" $REDIS_AUTH -n "$REDIS_DB_NUM" PING &>/dev/null; then
    error "Cannot connect to Redis at $REDIS_HOST:$REDIS_PORT"
    exit 1
fi
ok "Redis connection OK"

# ─── Step 1: Create dev DB + Grant Privileges ────────────────
info "Creating dev database: $DEV_DB"

$MARIADB_CMD <<SQL
CREATE DATABASE IF NOT EXISTS \`$DEV_DB\`
  CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
SQL
ok "Database $DEV_DB created (or already exists)"

# SELECT 권한 확인 (VIEW가 운영 DB 참조하려면 필요)
info "Verifying SELECT privilege on $PROD_DB..."
if $MARIADB_CMD -e "SELECT 1 FROM $PROD_DB.stock_masters LIMIT 1" &>/dev/null; then
    ok "SELECT on $PROD_DB OK"
else
    warn "Cannot SELECT from $PROD_DB — VIEWs will fail"
    warn "Run on MS-01: GRANT SELECT ON $PROD_DB.* TO '$DB_USER'@'%';"
fi

# ─── Step 2: Alembic Migration ───────────────────────────────
info "Running alembic upgrade head on $DEV_DB..."

cd "$PROJECT_DIR"

# .env.dev의 환경변수가 이미 로드됨 → alembic이 config.py를 통해 읽음
# Migration 004는 운영 DB 레거시 컬럼 수정용 — fresh DB에서는 불필요
# 003까지 실행 후 004를 stamp하고 나머지를 이어서 실행
uv run alembic upgrade 003
info "Stamping migration 004 (legacy column fix, not needed on fresh DB)..."
uv run alembic stamp 004
uv run alembic upgrade head
ok "Alembic migration complete"

# ─── Step 3: Replace Reference Tables with VIEWs ─────────────
info "Replacing 14 reference tables with VIEWs → $PROD_DB..."

if [ ! -f "$SQL_FILE" ]; then
    error "SQL file not found: $SQL_FILE"
    exit 1
fi

$MARIADB_CMD "$DEV_DB" < "$SQL_FILE"
ok "14 VIEWs created (referencing $PROD_DB)"

# ─── Step 4: Redis Safety Flags ──────────────────────────────
info "Setting Redis safety flags on DB $REDIS_DB_NUM..."

redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" $REDIS_AUTH -n "$REDIS_DB_NUM" SET trading_flags:dryrun 1 >/dev/null
redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" $REDIS_AUTH -n "$REDIS_DB_NUM" SET trading_flags:stop 1 >/dev/null
ok "trading_flags:dryrun=1, trading_flags:stop=1"

# ─── Verification ────────────────────────────────────────────
echo ""
info "Running verification checks..."

# Check VIEW works
VIEW_COUNT=$($MARIADB_CMD -N -e "SELECT COUNT(*) FROM $DEV_DB.stock_masters" 2>/dev/null || echo "0")
if [ "$VIEW_COUNT" -gt 0 ]; then
    ok "stock_masters VIEW: $VIEW_COUNT rows (from $PROD_DB)"
else
    warn "stock_masters VIEW returned 0 rows — check $PROD_DB data"
fi

# Check trade tables are real tables (not VIEWs)
for tbl in positions trade_logs daily_asset_snapshots; do
    TBL_TYPE=$($MARIADB_CMD -N -e "SELECT TABLE_TYPE FROM information_schema.TABLES WHERE TABLE_SCHEMA='$DEV_DB' AND TABLE_NAME='$tbl'" 2>/dev/null || echo "MISSING")
    if [ "$TBL_TYPE" = "BASE TABLE" ]; then
        ok "$tbl: independent table"
    else
        warn "$tbl: expected BASE TABLE, got $TBL_TYPE"
    fi
done

# Redis flags
DRYRUN_VAL=$(redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" $REDIS_AUTH -n "$REDIS_DB_NUM" GET trading_flags:dryrun 2>/dev/null || echo "?")
STOP_VAL=$(redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" $REDIS_AUTH -n "$REDIS_DB_NUM" GET trading_flags:stop 2>/dev/null || echo "?")
ok "Redis DB $REDIS_DB_NUM: dryrun=$DRYRUN_VAL, stop=$STOP_VAL"

# ─── Done ────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════"
echo -e "${GREEN}  Development testbed setup complete!${NC}"
echo "════════════════════════════════════════════════════════"
echo ""
echo "사용법:"
echo "  # 서비스 실행 (예: scanner)"
echo "  set -a && source .env.dev && set +a"
echo "  uv run python -m prime_jennie.services.scanner.app"
echo ""
echo "  # 또는 pytest"
echo "  set -a && source .env.dev && set +a"
echo "  uv run pytest tests/ -x"
echo ""
echo "주의:"
echo "  - DRY_RUN=true (기본) — 실주문 차단됨"
echo "  - trading_flags:stop=1 — 매매 중단 상태"
echo "  - dev에서 KIS API 직접 호출 시 토큰 rate limit 주의"
echo ""
