# Session Handoff - 2026-02-23 (System 페이지 탭 추가)

## 작업 요약 (What was done)

### System 페이지에 Workflows + Logs 탭 추가
레거시 my-prime-jennie의 Airflow DAG 관리, Loki 로그 뷰어를 prime-jennie 대시보드로 이식.

**Backend (2 신규 + 1 수정):**
- `prime_jennie/services/dashboard/routers/airflow.py` — Airflow REST API v2 프록시 (JWT 인증)
  - `GET /api/airflow/dags` — 활성 DAG 목록 + 최근 실행 상태
  - `POST /api/airflow/dags/{dag_id}/trigger` — DAG 수동 트리거
- `prime_jennie/services/dashboard/routers/logs.py` — Loki query_range 프록시
  - `GET /api/logs/stream?service=&limit=&start=&end=` — 서비스별 로그 조회
  - `GET /api/logs/services` — 14개 서비스 목록
- `prime_jennie/services/dashboard/app.py` — airflow, logs 라우터 등록

**Frontend (2 수정):**
- `frontend/src/lib/api.ts` — `AirflowDag`, `LogEntry` 타입 + `useAirflowDags()`, `triggerDag()`, `useLogServices()`, `useLogs()` 훅 추가
- `frontend/src/pages/System.tsx` — Services / Workflows / Logs 3탭 구조로 개편

### Airflow 3 마이그레이션
- 레거시 코드가 Airflow 2 REST API v1 기반이었으나, 현재 Airflow 3 사용 중
- `/api/v1` → `/api/v2` 전환, Basic Auth → JWT Bearer 토큰 인증
- 필드명 변경: `schedule_interval` → `timetable_summary`, `execution_date` → `logical_date`, `next_dagrun` → `next_dagrun_run_after`

## 커밋 이력
1. `dcbe1b5` — `feat: System 페이지에 Workflows(Airflow) + Logs(Loki) 탭 추가`
2. `5effd2e` — `fix: Airflow 3 REST API v2 + JWT 인증으로 마이그레이션`

## 현재 상태 (Current State)
- 배포 완료, 대시보드에서 3개 탭 모두 정상 동작 확인
- Workflows: DAG 목록 + 실행 상태 표시 + Trigger 버튼 동작
- Logs: 서비스 셀렉터 + 시간 범위(5m/30m/1h/6h) + 모노스페이스 뷰어
- 전체 테스트 546개 통과, ruff clean, 프론트엔드 빌드 성공

## 다음 할 일 (Next Steps)
- [ ] TODO.md 기존 항목 계속 진행 (ROE 갱신 Job, 분기 재무 갱신 등)
- [ ] Workflows 탭: paused DAG 토글 기능 추가 고려
- [ ] Logs 탭: 로그 검색/필터링, 자동 스크롤 개선 고려

## Context for Next Session
다음 세션 시작 시 아래 파일들을 먼저 읽어주세요:
- `.ai/TODO.md` — 미해결 과제 목록
- `prime_jennie/services/dashboard/routers/airflow.py` — Airflow v2 API 프록시 (JWT 토큰 캐시 로직)
- `prime_jennie/services/dashboard/routers/logs.py` — Loki 로그 프록시
- `frontend/src/pages/System.tsx` — 3탭 구조 (Services/Workflows/Logs)

## 핵심 결정사항 (Key Decisions)
- **Airflow 3 JWT 토큰 캐시**: 모듈 레벨 `_cached_token` 변수로 캐시, 401 응답 시 1회 자동 재발급. 프로세스 수명 동안 유효 (Airflow 토큰 만료 ~24h)
- **Loki 포트 3100**: 레거시는 3400이었으나 현재 promtail/Loki 설정이 3100 사용
- **서비스 목록 하드코딩**: logs.py의 `_SERVICES` 14개 — Loki label values API 대신 하드코딩 (안정성 우선)
- **프론트엔드 응답 형식 유지**: airflow.py가 v2 필드를 v1 이름(`schedule_interval`, `next_dagrun`, `last_run_date`)으로 매핑하여 프론트엔드 타입 변경 최소화

## 주의사항 (Warnings)
- `airflow.py`의 `_cached_token`은 global 변수 — 멀티 워커 환경에서는 워커별 독립 (현재 단일 워커라 문제 없음)
- 다른 창에서 `telegram/handler.py`, `.ai/TODO.md` 등 수정 중이므로 해당 파일 건드리지 말 것
- `frontend/tsconfig.tsbuildinfo`, `scripts/run_backtest.py`, `uv.lock`은 untracked 상태로 남아있음 (이 세션과 무관)
