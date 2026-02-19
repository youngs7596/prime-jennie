#!/usr/bin/env bash
set -euo pipefail

# 1) DB 마이그레이션 (Alembic 락 사용, 동시 실행 안전)
airflow db migrate

# 2) Admin 유저 생성 (이미 존재 시 무시)
airflow users create \
    --username admin --firstname Admin --lastname User \
    --role Admin --email admin@prime-jennie.local \
    --password "${AIRFLOW_ADMIN_PASSWORD:-admin}" || true

# 3) HTTP Connection 등록 (delete+add 패턴으로 멱등성 보장)
for conn in \
    "scout_job http 127.0.0.1 8087" \
    "job_worker http 127.0.0.1 8095" \
    "macro_council http 127.0.0.1 8089" \
    "price_monitor http 127.0.0.1 8088"; do
    set -- $conn
    airflow connections delete "$1" 2>/dev/null || true
    airflow connections add "$1" --conn-type "$2" --conn-host "$3" --conn-port "$4"
done

# 4) 원래 명령 실행 (api-server / scheduler)
exec "$@"
