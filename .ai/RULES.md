# AI Assistant Ground Rules

> **정본(Single Source of Truth)**: 이 파일 + 루트 `CLAUDE.md`
> 모든 LLM은 세션 시작 시 이 파일을 먼저 읽고 따릅니다.

---

## 프로젝트 개요

- **프로젝트명**: prime-jennie (my-prime-jennie 리빌드)
- **목적**: 주식/자산 자동 매매 시스템 (LLM 기반 판단 + 실제 트레이딩)
- **기술 스택**:
  - Backend: Python 3.12 (FastAPI, Pydantic v2, SQLModel)
  - Database: MariaDB, Redis (Streams, Cache)
  - LLM: vLLM (EXAONE 4.0), Claude, DeepSeek (CloudFailover)
  - Vector DB: Qdrant + KURE-v1 임베딩
  - Infra: Docker Compose, GitHub Actions CI/CD
  - Trading API: KIS (한국투자증권)
  - Frontend: React 18, TypeScript, Vite, Tailwind CSS

---

## 세션 시작 시 (Bootstrap)

### 1. 이전 세션 파일 확인
```
.ai/sessions/ 폴더에서 가장 최근 session-*.md 파일을 찾아 읽습니다.
```

### 2. 컨텍스트 로딩
- 세션 파일의 **"Context for Next Session"** 섹션에 명시된 파일들 확인
- **"Next Steps"** 에서 이어서 작업할 내용 파악

### 3. 사용자에게 브리핑
```
이전 세션 (YYYY-MM-DD)에서 [작업 내용]까지 진행했습니다.
다음 할 일: [목록]
이어서 진행할까요?
```

---

## 세션 종료 시 (Handoff)

사용자가 **"정리해줘"**, **"세션 저장"**, **"handoff"**, **"세션 종료"** 등을 말하면:

### 0. 커밋 여부 확인 (CRITICAL)
```bash
git status  # uncommitted 변경 확인
git ls-files --others --exclude-standard  # 신규 파일 확인
```

### 1. 세션 요약 파일 생성
- 파일 위치: `.ai/sessions/session-YYYY-MM-DD-HH-mm.md`

### 2. 포함할 내용
```markdown
# Session Handoff - YYYY-MM-DD-HH-mm

## 작업 요약 (What was done)
- 완료된 작업 목록
- 변경된 파일들과 변경 내용 요약

## 현재 상태 (Current State)
- 프로젝트의 현재 상태
- 알려진 이슈나 버그

## 다음 할 일 (Next Steps)
- [ ] 우선순위 높음
- [ ] 중간
- [ ] 나중에

## Context for Next Session
다음 세션 시작 시 아래 파일들을 먼저 읽어주세요:
- `경로/파일명` - 이유

## 핵심 결정사항 (Key Decisions)
- 왜 이런 방식을 선택했는지

## 주의사항 (Warnings)
- 건드리면 안 되는 것
- 의존성 이슈
```

---

## 핵심 파일 참조

| 경로 | 역할 |
|------|------|
| `CLAUDE.md` | Claude Code 시스템 지침 |
| `docker-compose.yml` | 전체 서비스 구성 (infra/real 프로필) |
| `prime_jennie/domain/` | 도메인 모델 (30+ Pydantic 모델) |
| `prime_jennie/infra/` | 인프라 레이어 (DB, Redis, LLM, KIS) |
| `prime_jennie/services/` | 마이크로서비스 (gateway, scout, scanner, buyer, seller, monitor, council, news, briefing, telegram, dashboard) |
| `dags/` | Airflow DAG 정의 |
| `prompts/` | LLM 프롬프트 (council, analyst, news, briefing) |
| `frontend/` | React 대시보드 |
| `tests/` | unit, contract, integration, e2e 테스트 |

---

## 빌드 / 테스트 / 실행

```bash
# 가상환경 활성화
source .venv/bin/activate

# 테스트
pytest tests/ -v                    # 전체
pytest tests/unit/ -v               # 유닛 테스트만
pytest tests/e2e/ -v                # E2E 테스트만

# 린트
ruff check .

# Docker
docker compose --profile infra up -d   # 인프라 (DB, Redis, vLLM, Qdrant)
docker compose --profile real up -d    # 트레이딩 서비스

# 로그
docker compose logs -f [서비스명]
```

---

## 검증 및 완료 보고 (배포 전 품질 게이트)

### 필수 검증 단계

1. **문법 검증**: `python -m py_compile [변경된_파일.py]`
2. **테스트 실행 (필수)**:
   ```bash
   pytest tests/unit/services/[관련_테스트].py -v
   pytest tests/ -x -q --tb=short  # 전체 빠른 체크
   ```
3. **검증 후 보고**: 정상 동작이 확인된 경우에만 사용자에게 완료를 보고합니다.

### 배포 승인 기준

- [ ] 관련 테스트 **전체 통과** (0 failures)
- [ ] 새 기능의 경우 **테스트 코드 포함**
- [ ] 린트 에러 없음 (`ruff check .`)

---

## 코드 손실 방지 규칙 (CRITICAL)

### 필수 체크리스트

1. **작업 완료 시 즉시 커밋**
2. **세션 종료 전 확인**
   ```bash
   git status
   git ls-files --others --exclude-standard  # 신규 파일!
   git diff --stat
   ```
3. **신규 파일은 특히 주의** — `git diff`에 안 보임, `--others` 필수

### 코드 손실 시나리오

| 시나리오 | 결과 | 방지법 |
|---------|------|--------|
| 커밋 없이 Docker만 배포 | 재빌드 시 코드 사라짐 | 배포 전 커밋 |
| git checkout/pull | uncommitted 파일 덮어쓰기 | stash 또는 커밋 |
| 신규 파일 git add 누락 | 커밋에 포함 안 됨 | `--others` 확인 |

---

## 위험 작업 제한

아래 작업은 **반드시 사용자 승인 후** 실행:

- 파일/디렉토리 삭제 (`rm -rf` 등)
- 데이터베이스 마이그레이션 변경
- 환경변수/시크릿 파일 수정 (`.env`, `secrets.*`)
- 10개 이상의 파일을 동시에 수정하는 대규모 리팩토링

### 장중 배포 금지 (CRITICAL)
- **주식 장 운영 시간 (09:00~15:30 KST)에는 `git push`를 하지 않는다**
- push → GitHub Actions Deploy가 자동 트리거되어 서비스가 재시작됨
- 장중 재시작 시 Gateway WebSocket 끊김 → 틱 수신 중단 → 매수/매도 불가
- 장중에는 커밋까지만 하고, **push는 장 종료(15:30) 이후에 수행**
- 긴급 핫픽스가 필요한 경우 반드시 사용자 승인 후 push
- **장중 kis-gateway 재시작 절대 금지** — 토큰 캐시 소실 시 KIS API 토큰 발급 rate limit(403)에 걸려 전체 매매 중단됨. 다른 서비스 단일 배포는 가능하나 gateway는 예외

---

## 커뮤니케이션 규칙

- 한국어로 대화
- 사고 과정도 한국어로 표기
- 코드 주석은 한국어 (기존 스타일 따름)
- 작업 전 계획 공유, 작업 후 결과 요약

---

## 아키텍처 핵심 패턴

- **도메인 모델 중심**: `prime_jennie.domain`에 30+ Pydantic 모델 정의, 서비스 간 공유
- **설정**: Pydantic Settings (`domain/config.py`) — env prefix 기반 자동 매핑
- **메시징**: Redis Streams (`infra/redis/streams.py`) — TypedStreamPublisher/Consumer
- **캐시**: Redis JSON (`infra/redis/cache.py`) — TypedCache[T]
- **LLM**: 3-tier (FAST/REASONING/THINKING), Factory 패턴 (`infra/llm/factory.py`)
- **서비스 기반**: `create_app()` 팩토리 (`services/base.py`) — 공통 /health, 에러 핸들러
