# Session Handoff - 2026-02-25 CI/Deploy 경로 필터 적용

## 작업 요약 (What was done)

### CI/Deploy 워크플로우에 경로 필터 적용

비코드 파일(`.ai/`, `docs/`, `*.md`) 변경 시 불필요한 CI/배포가 실행되는 문제를 해결.
`dorny/paths-filter@v3`을 사용하여 변경된 파일에 따라 선택적으로 job을 실행하도록 개선.

**수정 파일 (2개):**

#### 1. `.github/workflows/ci.yml`
- `paths-ignore` 추가: `.ai/**`, `docs/**`, `*.md`, `LICENSE` 변경 시 CI 전체 스킵
- `detect-changes` job 추가 (`dorny/paths-filter@v3`로 python/frontend 변경 감지)
- 조건부 실행:
  - `lint-and-type-check`: python 또는 frontend 변경 시
  - `test-unit`, `test-e2e`: python 변경 시만
  - `frontend-build`: frontend 변경 시만

#### 2. `.github/workflows/deploy.yml`
- `paths` 필터 추가: 서비스 코드 변경 시만 deploy 트리거
- `detect-changes` job 추가 (backend/frontend 변경 감지)
- 선택적 배포:
  - backend + frontend → `docker compose --profile real up -d --build` (기존 동작)
  - backend만 → 14개 backend 서비스만 빌드+재시작
  - frontend만 → `dashboard-frontend`만 빌드+재시작

## 동작 매트릭스

| 변경 파일 | CI lint | CI test | CI frontend | Deploy |
|-----------|:-------:|:-------:|:-----------:|:------:|
| `.ai/**`, `docs/**`, `*.md` | 스킵 | 스킵 | 스킵 | 스킵 |
| `prime_jennie/**` | 실행 | 실행 | 스킵 | Backend만 |
| `tests/**` | 실행 | 실행 | 스킵 | 스킵 |
| `frontend/**` | 실행 | 스킵 | 실행 | Frontend만 |
| `docker-compose.yml`, `Dockerfile` | 스킵 | 스킵 | 스킵 | 전체 |
| 혼합 (python + frontend) | 실행 | 실행 | 실행 | 전체 |

## 현재 상태 (Current State)
- development 브랜치, push 완료
- 이 커밋 자체가 `.github/workflows/` 변경이므로 CI는 트리거됨 (paths-ignore에 해당 안 함)
- Deploy는 `.github/workflows/`가 paths 목록에 없으므로 스킵될 것 (의도대로)

## 검증 방법
1. 이 커밋 push 후: CI 트리거 O, Deploy 트리거 X 확인
2. 이후 docs-only 커밋 시: CI, Deploy 모두 스킵 확인
3. prime_jennie/ 변경 시: CI lint+test O, Deploy backend만 확인

## 다음 할 일 (Next Steps)
- [ ] Scout 실행 후 RAG 뉴스 프롬프트 주입 효과 확인
- [ ] LLM Usage 대시보드 정상 집계 확인
- [ ] Qdrant payload index 추가
- [ ] 월간 ROE 갱신 Job 구현

## Context for Next Session
- `.github/workflows/ci.yml` — CI 경로 필터 + 조건부 job
- `.github/workflows/deploy.yml` — Deploy 경로 필터 + 선택적 배포
- `.ai/sessions/session-2026-02-25-dashboard-fixes.md` — 이전 세션
