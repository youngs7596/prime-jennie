# Session Handoff - 2026-02-27 GHCR Pull 기반 배포 전환 + v0.1.0 릴리스

## 작업 요약 (What was done)

배포 파이프라인을 **소스 빌드 → GHCR pull 방식**으로 전환하고, v0.1.0 릴리스 태그를 생성했다.

### 1. ghcr.yml 수정 — 빌드 + 배포 통합 워크플로우

- `branches: [main]` → `[main, development]` (development push 시에도 이미지 빌드)
- `paths`에 `docker-compose.yml` 추가
- **deploy job 추가**:
  - `needs: [build-backend, build-frontend]` — 이미지 빌드 완료 대기
  - `if: github.ref == 'refs/heads/development'` — development만 자동 배포
  - CI 성공 대기 gate: `check-runs` API 폴링으로 `lint-and-type-check`, `test-unit`, `test-e2e` 통과 확인
  - GHCR 로그인 → `docker compose --profile real pull` → `up -d`
  - `concurrency: deploy-production` (배포 직렬화)

### 2. deploy.yml 수정 — 수동 폴백 전용

- push 트리거 제거, `workflow_dispatch`만 남김
- `detect-changes` job 제거 — 수동 실행 시 항상 full `--build`
- 긴급 상황(GHCR 장애 등)에서 소스 빌드 방식으로 수동 배포 가능

### 3. README.md 업데이트

- 서비스 실행 명령어에서 `--build` 플래그 3곳 제거
- 기술 스택에 GHCR 표기 추가

### 4. v0.1.0 태그 생성

- main 브랜치에 `v0.1.0` 태그 → GHCR에 `v0.1.0`, `0.1` 태그 이미지 빌드 완료

## 검증 결과

| 항목 | 결과 |
|------|------|
| GHCR 이미지 빌드 (backend 20s + frontend 43s) | PASS |
| deploy job (CI gate + pull + up, 40s) | PASS |
| 전체 서비스 healthy (GHCR pull 이미지 기반) | PASS |
| v0.1.0 태그 빌드 (semver 이미지) | PASS |
| workflow 파일만 변경 시 GHCR 미트리거 확인 | PASS |
| E2E 테스트 (Mock KIS, 47 passed / 0.44s) | PASS |

## 배포 플로우 (변경 후)

```
development push
  → [CI] lint + test (ubuntu-latest)
  → [GHCR Publish] build-backend + build-frontend (ubuntu-latest, GHA cache)
  → [deploy] CI 성공 대기 → GHCR login → pull → up -d (self-hosted)
```

## 커밋 목록

| SHA | 메시지 |
|-----|--------|
| `9f65d1c` | ci: GHCR pull 기반 배포 전환 — ghcr.yml에 deploy job 통합 |
| `6374ee8` | ci: deploy job에 CI 성공 대기 gate 추가 |
| `80ce416` | docs: README에서 --build 플래그 제거 — GHCR pull 방식 반영 |

## 현재 상태 (Current State)

- **development**: `80ce416` (main과 동기화 완료)
- **main**: `80ce416` (fast-forward merge)
- **v0.1.0 태그**: main에서 생성, GHCR에 이미지 빌드 완료
- 서비스: GHCR `latest` 이미지로 정상 구동 중

## GHCR 이미지 태그

| 태그 | 용도 |
|------|------|
| `ghcr.io/youngs7596/prime-jennie:latest` | main 최신 |
| `ghcr.io/youngs7596/prime-jennie:sha-xxxxxxx` | 커밋별 |
| `ghcr.io/youngs7596/prime-jennie:v0.1.0` | 릴리스 버전 |
| `ghcr.io/youngs7596/prime-jennie:0.1` | major.minor |
| `ghcr.io/youngs7596/prime-jennie-frontend:*` | 동일 태그 체계 |

## 다음 할 일 (Next Steps)

- [ ] GitHub Release 생성 (v0.1.0 태그에 릴리스 노트 작성)
- [ ] `gh` CLI 토큰에 `read:packages` 스코프 추가 (GHCR 패키지 목록 조회용)
- [ ] `.github/workflows/` 변경도 GHCR 빌드를 트리거하도록 paths에 추가할지 검토

## Context for Next Session

- `.github/workflows/ghcr.yml` — 빌드 + 배포 통합 워크플로우 (deploy job에 CI gate 포함)
- `.github/workflows/deploy.yml` — workflow_dispatch 수동 폴백
- `.ai/sessions/session-2026-02-27-public-release-prep.md` — 이전 세션 (public release 준비)
