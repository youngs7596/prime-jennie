# Session Handoff: 서비스 기동 경쟁 조건 + Loki 권한 수정

## 작업 날짜
2026-02-23 (월요일, 장 운영일)

## 작업 브랜치
`development`

## 완료된 작업

### 1. Redis 기동 경쟁 조건 해소
- **문제**: 재배포 시 Redis가 준비되기 전에 Monitor/Scanner의 tick consumer가 `xgroup_create` 실패 → 스레드 즉사 후 복구 불가
- **수정**: `BusyLoadingError`/`ConnectionError` 시 최대 30초 대기 재시도 루프 적용
- **파일**: `prime_jennie/services/monitor/app.py`, `prime_jennie/services/scanner/app.py`

### 2. Monitor `/start`, `/stop` 엔드포인트 추가
- **문제**: Airflow DAG `price_monitor_ops`(09:00)와 `price_monitor_stop_ops`(15:30)가 호출하는 `/start`, `/stop` 엔드포인트가 Monitor에 없어 404
- **수정**:
  - `POST /start`: 포지션 전체 새로고침 + Gateway 구독
  - `POST /stop`: 장 마감 ACK (tick consumer는 유지)

### 3. Scanner watchlist 리로드 시 Gateway 재구독
- **문제**: 5분 주기 watchlist 리로드 후 Gateway 구독을 다시 안 함 → 새 종목 틱 수신 불가
- **수정**: 리로드 후 `_subscribe_to_gateway()` 호출 추가

### 4. Loki 볼륨 권한 수정
- **문제**: 2025-12-26~2026-02-19 동안 Loki가 root로 실행되어 root 소유 디렉토리 58개 생성. 2026-02-19에 `user: "1000"` 복구 후에도 잔여 root 파일 때문에 index upload/WAL checkpoint 실패 → **09:00~18:15 KST 약 9시간 로그 소실**
- **수정**: `sudo chown -R 1000:1000 /docker_data/loki_data/` + Loki 재시작
- **근본 원인**: 과거 docker-compose에서 `user: "1000"` 빠진 기간의 잔재 (GitHub Actions runner와 무관)

## 커밋
- `f47dfed` — `fix: Redis 기동 경쟁 조건 해소 + monitor /start /stop 엔드포인트 추가`

## 배포 상태
- development push 완료 → GitHub Actions 자동 배포 트리거됨
- **재부팅 예정** → 다음 세션에서 시스템 동작 확인 필요

## 다음 세션 TODO
- [x] 재부팅 후 전체 서비스 정상 기동 확인 (docker compose ps) ✅
- [x] Monitor/Scanner tick consumer Redis 연결 정상 확인 ✅
- [x] Airflow DAG `/start`, `/stop` 정상 호출 확인 ✅
- [x] Loki 로그 공백 없이 정상 수집 확인 (Grafana) ✅
- [x] 장중 매매 정상 발생 여부 확인 ✅
