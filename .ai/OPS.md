# 운영/디버깅 가이드 (Operations Playbook)

> **목적**: AI 어시스턴트가 시스템 상태 확인/디버깅 시 매번 시행착오 없이 바로 사용할 수 있는 검증된 명령어 모음.
> 이 파일의 명령어는 모두 **WSL2 개발 머신**에서 실행하며, 서버(MS-01)로는 `ssh prime-jennie` 경유.

---

## 기본 전제

| 항목 | 값 |
|------|-----|
| **운영 서버** | MS-01 (hostname: `prime-jennie`, IP: 192.168.31.195) |
| **개발 머신** | WSL2 (Docker 컨테이너 없음 — 모든 서비스는 MS-01에서 실행) |
| **프로젝트 경로 (서버)** | `/home/youngs75/projects/prime-jennie` |
| **컨테이너 prefix** | `prime-jennie-{서비스명}-1` |
| **DB password** | `.env`의 `DB_PASSWORD` (특수문자 `$` 포함 주의) |
| **Redis password** | `.env`의 `REDIS_PASSWORD` |

### SSH 접속 패턴

```bash
# 단일 명령
ssh prime-jennie "명령어"

# 복잡한 명령 (특수문자, 따옴표 중첩 시) — heredoc 사용
ssh prime-jennie << 'EOSSH'
복잡한 명령어
EOSSH
```

---

## 1. 서비스 상태 확인

### 전체 컨테이너 상태
```bash
ssh prime-jennie "docker ps --format 'table {{.Names}}\t{{.Status}}' | sort"
```

### 특정 서비스 상태
```bash
ssh prime-jennie "docker ps --format '{{.Names}} {{.Status}}' | grep news"
```

### 컨테이너 이름 목록 (22개)
```
prime-jennie-airflow-scheduler-1    prime-jennie-airflow-webserver-1
prime-jennie-buy-executor-1         prime-jennie-buy-scanner-1
prime-jennie-cloudflared-1          prime-jennie-dashboard-1
prime-jennie-dashboard-frontend-1   prime-jennie-grafana-1
prime-jennie-job-worker-1           prime-jennie-kis-gateway-1
prime-jennie-loki-1                 prime-jennie-mariadb-1
prime-jennie-news-pipeline-1        prime-jennie-price-monitor-1
prime-jennie-promtail-1             prime-jennie-qdrant-1
prime-jennie-redis-1                prime-jennie-scout-job-1
prime-jennie-sell-executor-1        prime-jennie-telegram-1
prime-jennie-vllm-embed-1           prime-jennie-vllm-llm-1
```

---

## 2. 로그 조회

### 기본 패턴
```bash
# 최근 N줄
ssh prime-jennie "docker logs prime-jennie-{서비스명}-1 --tail 50"

# 최근 N시간
ssh prime-jennie "docker logs prime-jennie-{서비스명}-1 --since 2h --tail 100"

# 에러만 필터
ssh prime-jennie "docker logs prime-jennie-{서비스명}-1 --since 1h 2>&1 | grep -i error | tail -20"

# 실시간 follow (Ctrl+C로 종료)
ssh prime-jennie "docker logs prime-jennie-{서비스명}-1 -f --tail 10"
```

### 서비스별 자주 쓰는 로그 조회

```bash
# News Pipeline — collector/analyzer/archiver 동작 확인
ssh prime-jennie "docker logs prime-jennie-news-pipeline-1 --since 2h 2>&1 | grep -E 'analyzer|archiver|collector cycle|error|ERROR' | tail -30"

# Job Worker — Council/WSJ/Intraday Risk
ssh prime-jennie "docker logs prime-jennie-job-worker-1 --since 2h 2>&1 | grep -E 'council|wsj|intraday|risk|error' | tail -30"

# KIS Gateway — 구독/폴링 상태
ssh prime-jennie "docker logs prime-jennie-kis-gateway-1 --since 1h 2>&1 | grep -E 'subscri|polling|error|market' | tail -30"

# Scanner — 매수 시그널
ssh prime-jennie "docker logs prime-jennie-buy-scanner-1 --since 1h 2>&1 | grep -E 'signal|buy|strategy|error' | tail -30"

# Monitor — Exit Rules / 매도 시그널
ssh prime-jennie "docker logs prime-jennie-price-monitor-1 --since 1h 2>&1 | grep -E 'exit|sell|rule|forced|error' | tail -30"

# vLLM — 추론 에러
ssh prime-jennie "docker logs prime-jennie-vllm-llm-1 --since 1h 2>&1 | grep -E 'error|ERROR|400|500|OOM' | tail -20"
```

---

## 3. MariaDB 조회

### 핵심: heredoc 패턴 사용 (특수문자 안전)

```bash
ssh prime-jennie << 'EOSSH'
docker exec prime-jennie-mariadb-1 mariadb -u root -p'q1w2e3R$' jennie_db -e "SQL문"
EOSSH
```

> **주의**: `ssh prime-jennie "docker exec ... -p'q1w2e3R$' ..."` 형태는 `$` 이스케이핑 문제 발생.
> 반드시 **heredoc (`<< 'EOSSH'`)** 사용. 따옴표로 감싼 EOSSH는 변수 치환을 방지.

### 자주 쓰는 쿼리

```bash
# 감성 분석 점수 분포 (날짜별)
ssh prime-jennie << 'EOSSH'
docker exec prime-jennie-mariadb-1 mariadb -u root -p'q1w2e3R$' jennie_db -e "
SELECT news_date, COUNT(*) as cnt,
  SUM(CASE WHEN sentiment_score <> 50 THEN 1 ELSE 0 END) as non_50,
  ROUND(AVG(sentiment_score),1) as avg_score
FROM stock_news_sentiments
WHERE news_date >= CURDATE() - INTERVAL 3 DAY
GROUP BY news_date ORDER BY news_date"
EOSSH

# 포지션 현황
ssh prime-jennie << 'EOSSH'
docker exec prime-jennie-mariadb-1 mariadb -u root -p'q1w2e3R$' jennie_db -e "
SELECT p.stock_code, s.name, p.quantity, p.avg_price, p.current_price,
  ROUND((p.current_price/p.avg_price - 1)*100, 2) as profit_pct
FROM positions p JOIN stock_masters s ON p.stock_code = s.stock_code
WHERE p.quantity > 0 ORDER BY profit_pct DESC"
EOSSH

# 최근 거래 로그
ssh prime-jennie << 'EOSSH'
docker exec prime-jennie-mariadb-1 mariadb -u root -p'q1w2e3R$' jennie_db -e "
SELECT trade_date, stock_code, side, quantity, price, sell_reason
FROM trade_logs ORDER BY trade_date DESC LIMIT 20"
EOSSH

# 테이블 목록
ssh prime-jennie << 'EOSSH'
docker exec prime-jennie-mariadb-1 mariadb -u root -p'q1w2e3R$' jennie_db -e "SHOW TABLES"
EOSSH

# 테이블 스키마 확인
ssh prime-jennie << 'EOSSH'
docker exec prime-jennie-mariadb-1 mariadb -u root -p'q1w2e3R$' jennie_db -e "DESCRIBE 테이블명"
EOSSH

# Alembic 버전 확인
ssh prime-jennie << 'EOSSH'
docker exec prime-jennie-mariadb-1 mariadb -u root -p'q1w2e3R$' jennie_db -e "SELECT * FROM alembic_version_app"
EOSSH
```

---

## 4. Redis 조회

### 기본 패턴

```bash
ssh prime-jennie "docker exec prime-jennie-redis-1 redis-cli -a '21JPENkajUCjPr7kMkg-vJ4705iXtAGJ' --no-auth-warning 명령어"
```

> Redis password에 특수문자가 없으므로 일반 인라인 가능.

### 자주 쓰는 Redis 명령

```bash
# Trading flags (stop/pause/dryrun)
ssh prime-jennie "docker exec prime-jennie-redis-1 redis-cli -a '21JPENkajUCjPr7kMkg-vJ4705iXtAGJ' --no-auth-warning MGET trading_flags:stop trading_flags:pause trading_flags:dryrun"

# TradingContext (현재 국면)
ssh prime-jennie "docker exec prime-jennie-redis-1 redis-cli -a '21JPENkajUCjPr7kMkg-vJ4705iXtAGJ' --no-auth-warning GET cache:trading_context"

# Watchlist (현재 감시 종목)
ssh prime-jennie "docker exec prime-jennie-redis-1 redis-cli -a '21JPENkajUCjPr7kMkg-vJ4705iXtAGJ' --no-auth-warning GET cache:hot_watchlist"

# Stream 길이 + Consumer Group lag
ssh prime-jennie "docker exec prime-jennie-redis-1 redis-cli -a '21JPENkajUCjPr7kMkg-vJ4705iXtAGJ' --no-auth-warning XLEN stream:news:raw"
ssh prime-jennie "docker exec prime-jennie-redis-1 redis-cli -a '21JPENkajUCjPr7kMkg-vJ4705iXtAGJ' --no-auth-warning XINFO GROUPS stream:news:raw"

# buy-signals stream 확인 (밀린 시그널 여부)
ssh prime-jennie "docker exec prime-jennie-redis-1 redis-cli -a '21JPENkajUCjPr7kMkg-vJ4705iXtAGJ' --no-auth-warning XLEN stream:buy-signals"
ssh prime-jennie "docker exec prime-jennie-redis-1 redis-cli -a '21JPENkajUCjPr7kMkg-vJ4705iXtAGJ' --no-auth-warning XINFO GROUPS stream:buy-signals"

# Intraday risk level
ssh prime-jennie "docker exec prime-jennie-redis-1 redis-cli -a '21JPENkajUCjPr7kMkg-vJ4705iXtAGJ' --no-auth-warning GET intraday:risk:recovery_start"

# 강제 청산 상태
ssh prime-jennie "docker exec prime-jennie-redis-1 redis-cli -a '21JPENkajUCjPr7kMkg-vJ4705iXtAGJ' --no-auth-warning SMEMBERS forced_liquidation:stocks"
ssh prime-jennie "docker exec prime-jennie-redis-1 redis-cli -a '21JPENkajUCjPr7kMkg-vJ4705iXtAGJ' --no-auth-warning GET forced_liquidation:armed"

# 키 검색 (패턴)
ssh prime-jennie "docker exec prime-jennie-redis-1 redis-cli -a '21JPENkajUCjPr7kMkg-vJ4705iXtAGJ' --no-auth-warning KEYS 'trading_flags:*'"
ssh prime-jennie "docker exec prime-jennie-redis-1 redis-cli -a '21JPENkajUCjPr7kMkg-vJ4705iXtAGJ' --no-auth-warning KEYS 'cache:*'"

# 모든 stream 목록 + 길이
ssh prime-jennie "docker exec prime-jennie-redis-1 redis-cli -a '21JPENkajUCjPr7kMkg-vJ4705iXtAGJ' --no-auth-warning KEYS 'stream:*'"
```

### Redis 축약 패턴 (변수 활용)

```bash
# 셸 변수로 축약 (interactive 세션에서)
RCLI="docker exec prime-jennie-redis-1 redis-cli -a '21JPENkajUCjPr7kMkg-vJ4705iXtAGJ' --no-auth-warning"
ssh prime-jennie "$RCLI MGET trading_flags:stop trading_flags:pause"
```

---

## 5. Airflow DAG 관리

### DAG 목록
```bash
ssh prime-jennie "docker exec prime-jennie-airflow-webserver-1 airflow dags list -o table 2>/dev/null"
```

### DAG 수동 트리거
```bash
# 특정 DAG 실행
ssh prime-jennie "docker exec prime-jennie-airflow-webserver-1 airflow dags trigger {dag_id}"

# 예시: Council 수동 실행
ssh prime-jennie "docker exec prime-jennie-airflow-webserver-1 airflow dags trigger macro_council"

# 예시: Scout 수동 실행
ssh prime-jennie "docker exec prime-jennie-airflow-webserver-1 airflow dags trigger scout_pipeline"
```

### DAG 최근 실행 상태 확인
```bash
# 특정 DAG의 최근 실행 이력
ssh prime-jennie "docker exec prime-jennie-airflow-webserver-1 airflow dags list-runs -d {dag_id} -o table --limit 5 2>/dev/null"

# 예시: macro_council 최근 실행
ssh prime-jennie "docker exec prime-jennie-airflow-webserver-1 airflow dags list-runs -d macro_council -o table --limit 5 2>/dev/null"
```

### DAG pause / unpause
```bash
ssh prime-jennie "docker exec prime-jennie-airflow-webserver-1 airflow dags pause {dag_id}"
ssh prime-jennie "docker exec prime-jennie-airflow-webserver-1 airflow dags unpause {dag_id}"
```

### Job Worker 로그 (Council, Briefing, WSJ 등 실행 로그)

Airflow DAG은 job-worker의 HTTP 엔드포인트를 호출하므로, **실제 실행 로그는 job-worker 컨테이너**에 있음:

```bash
# Council 실행 로그
ssh prime-jennie "docker logs prime-jennie-job-worker-1 --since 2h 2>&1 | grep -E 'council|sentiment|regime|score' | tail -30"

# Macro quick (Intraday Risk) 로그
ssh prime-jennie "docker logs prime-jennie-job-worker-1 --since 1h 2>&1 | grep -E 'intraday|KOSPI|VIX|multiplier|risk' | tail -20"

# WSJ 수집 로그
ssh prime-jennie "docker logs prime-jennie-job-worker-1 --since 6h 2>&1 | grep -E 'wsj|WSJ|gmail|newsletter' | tail -20"

# Briefing 로그
ssh prime-jennie "docker logs prime-jennie-job-worker-1 --since 24h 2>&1 | grep -E 'briefing|report' | tail -20"
```

### 주요 DAG 목록

| DAG | schedule | 엔드포인트 | 역할 |
|-----|----------|-----------|------|
| `enhanced_macro_collection` | 07:40,11:40 KST | job-worker `/jobs/macro-collect` | 매크로 데이터 수집 |
| `macro_council` | 07:50,11:50 KST | job-worker `/jobs/council-trigger` | 3인 전문가 분석 |
| `enhanced_macro_quick` | */5 9-15 KST | job-worker `/jobs/macro-quick` | 장중 Intraday Risk |
| `scout_pipeline` | 08:30-14:30, 1h | scout-job `/scout/run` | AI 종목 발굴 |
| `daily_briefing_report` | 17:00 KST | job-worker `/report` | 일일 브리핑 |
| `daily_asset_snapshot` | 15:45 KST | job-worker `/jobs/asset-snapshot` | 자산 스냅샷 |
| `contract_smoke_test` | 21:00 KST | job-worker `/jobs/smoke-test` | 크롤러 검증 |

---

## 6. 서비스 API 직접 호출

서비스는 `network_mode: host`이므로 서버에서 localhost로 직접 호출 가능:

```bash
# Health check
ssh prime-jennie "curl -s localhost:8095/health"   # job-worker
ssh prime-jennie "curl -s localhost:8080/health"   # kis-gateway
ssh prime-jennie "curl -s localhost:8092/health"   # news-pipeline

# KIS Gateway — 실시간 상태
ssh prime-jennie "curl -s localhost:8080/api/realtime/status | python3 -m json.tool"

# KIS Gateway — 장 오픈 여부
ssh prime-jennie "curl -s localhost:8080/api/market/is-market-open | python3 -m json.tool"

# Dashboard — 포트폴리오
ssh prime-jennie "curl -s localhost:8090/api/portfolio | python3 -m json.tool"

# Dashboard — 매크로 현황
ssh prime-jennie "curl -s localhost:8090/api/macro | python3 -m json.tool"

# Council insight (최신 결과)
ssh prime-jennie "curl -s localhost:8095/jobs/council-insight | python3 -m json.tool"

# Council 수동 트리거
ssh prime-jennie "curl -s -X POST localhost:8095/jobs/council-trigger | python3 -m json.tool"

# Scout 수동 실행
ssh prime-jennie "curl -s -X POST localhost:8087/scout/run | python3 -m json.tool"
```

---

## 7. 시스템 리소스 확인

```bash
# CPU / Memory / Swap
ssh prime-jennie "free -h && echo '---' && uptime"

# GPU (vLLM)
ssh prime-jennie "nvidia-smi --query-gpu=utilization.gpu,utilization.memory,memory.used,memory.total --format=csv,noheader"

# Disk
ssh prime-jennie "df -h / /docker_data 2>/dev/null || df -h /"

# Docker 디스크 사용량
ssh prime-jennie "docker system df"
```

---

## 8. 긴급 운영

### 매매 정지 / 재개
```bash
# 정지 (텔레그램 /stop 과 동일)
ssh prime-jennie "docker exec prime-jennie-redis-1 redis-cli -a '21JPENkajUCjPr7kMkg-vJ4705iXtAGJ' --no-auth-warning SET trading_flags:stop 1"

# 재개
ssh prime-jennie "docker exec prime-jennie-redis-1 redis-cli -a '21JPENkajUCjPr7kMkg-vJ4705iXtAGJ' --no-auth-warning DEL trading_flags:stop"
```

### 특정 서비스만 재시작
```bash
# 장중 kis-gateway 재시작 절대 금지!
ssh prime-jennie "cd /home/youngs75/projects/prime-jennie && docker compose restart {서비스명}"

# 예: news-pipeline만 재시작
ssh prime-jennie "cd /home/youngs75/projects/prime-jennie && docker compose restart news-pipeline"
```

### Stream 정리 (lag 해소)
```bash
# Stream 길이 TRIM
ssh prime-jennie "docker exec prime-jennie-redis-1 redis-cli -a '21JPENkajUCjPr7kMkg-vJ4705iXtAGJ' --no-auth-warning XTRIM stream:news:raw MAXLEN 10000"

# Consumer group position 리셋 (미래 메시지만 처리)
ssh prime-jennie "docker exec prime-jennie-redis-1 redis-cli -a '21JPENkajUCjPr7kMkg-vJ4705iXtAGJ' --no-auth-warning XGROUP SETID stream:news:raw group_analyzer '$'"
```

---

## 9. 흔한 실수 방지

| 실수 | 원인 | 해결 |
|------|------|------|
| `Syntax error: ")" unexpected` | DB password `$` 이스케이핑 | heredoc `<< 'EOSSH'` 사용 |
| `No such container` | WSL2에서 직접 docker 실행 | `ssh prime-jennie` 경유 |
| `Access denied` | 잘못된 DB password | `q1w2e3R$` (heredoc 내) |
| Redis `NOAUTH` | password 누락 | `-a 'password' --no-auth-warning` |
| Airflow 출력 지저분 | stderr 경고 | `2>/dev/null` 추가 |
| 컨테이너 이름 모름 | - | prefix `prime-jennie-{서비스}-1` |
