# Session Handoff - 2026-02-27 Public Release 준비 (Phase 1+2)

## 작업 요약 (What was done)

prime-jennie를 public repository로 전환하기 위한 블로커 해결. 신규 사용자가 `git clone → install.sh → 실행`까지 도달할 수 있도록 하드코딩 제거 및 설정 외부화.

### Phase 1: Critical Blockers

#### 1-1. Stock Master 시딩 스크립트
- **신규**: `scripts/seed_stock_masters.py` — pykrx + 네이버 섹터로 KOSPI/KOSDAQ 전 종목 자동 시딩
  - `--dry-run` 옵션으로 DB 없이 결과 미리 확인 가능
  - `--market KOSPI|KOSDAQ` 선택 가능
  - pykrx `get_market_ticker_list()` → 종목명 → `get_market_cap_by_ticker()` → 시총 → `build_naver_sector_mapping()` → 섹터
  - 100건마다 배치 커밋, INSERT/UPDATE 분리
- **추가**: `services/jobs/app.py` — `/jobs/seed-stock-masters` 엔드포인트

#### 1-2. install.sh 수정
- `--profile trading` → `--profile real` 수정 (프로파일명 오류)
- Step 5 시딩 단계 추가 (DB 마이그레이션 직후)
- GPU 자동 감지: `nvidia-smi` 있으면 vLLM 시작 제안, 없으면 Cloud 모드 안내
- Airflow 시크릿 자동 생성 (install 시 .env에 Fernet/Secret/JWT 키 생성)

#### 1-3. .env.example 전면 업데이트
- `DOCKER_DATA_DIR` 섹션 추가
- Scout v2.3 설정 추가: `SCOUT_MA_WINDOW`, `SCOUT_ENTRY_THRESHOLD`, `SCOUT_EXIT_THRESHOLD`, `SCOUT_MIN_MARKET_CAP`, `SCOUT_HISTORY_RETENTION_DAYS`, `SCOUT_UNIVERSE_MARKET`
- `SCANNER_CONVICTION_ENTRY_ENABLED=false` (현행 운영값 반영)
- `LLM_EMBED_MODEL`, `LLM_EMBED_PROVIDER` 추가
- Airflow 시크릿 섹션 추가 (생성 명령어 가이드 포함)
- GPU/Cloud 모드 가이드 코멘트

#### 1-4. Docker 볼륨 경로 변수화
- `docker-compose.yml` 9곳: `/docker_data/xxx` → `${DOCKER_DATA_DIR:-/docker_data}/xxx`
- 기본값 = 현재 운영 경로 (기존 동작 무변경)

#### 1-5. Airflow 시크릿 외부화
- `docker-compose.yml` 6곳: 평문 Fernet/Secret/JWT → `${AIRFLOW_FERNET_KEY}` 등 env var
- 기존 .env에 현재 사용 중인 키 값 추가 (배포 연속성 보장)

### Phase 2: GPU-Free 지원

#### 2-1. vLLM 프로파일 분리 + No-GPU 오버라이드
- `docker-compose.yml`: vLLM 서비스 profiles `["infra"]` → `["gpu"]` 변경
  - GPU 사용자: `docker compose --profile infra --profile gpu --profile real up -d`
  - No-GPU 사용자: `docker compose -f docker-compose.yml -f docker-compose.no-gpu.yml --profile infra --profile real up -d`
- **신규**: `docker-compose.no-gpu.yml` — FAST tier를 deepseek_cloud, embed를 openai로 전환
- `deploy.yml`: `--profile gpu` 추가 (기존 서버 호환)

#### 2-2. 임베딩 모델 설정 가능화
- `config.py` LLMConfig: `embed_model` + `embed_provider` 필드 추가
- `ollama.py`: `generate_embeddings()` — config에서 모델명 읽기
- `archiver.py`: embed_provider 분기 (vllm → vLLM local, openai → OpenAI API)
- `rag_retriever.py`: 동일 분기 + Qdrant URL도 config에서 읽기

#### 2-3. Universe 마켓 설정 가능화
- `config.py` ScoutConfig: `universe_market` 필드 추가 (기본값 "KOSPI")
- `universe.py`: 하드코딩 `market="KOSPI"` → `config.universe_market`

## 변경 파일 목록

| 파일 | 변경 유형 |
|------|----------|
| `scripts/seed_stock_masters.py` | **신규** |
| `docker-compose.no-gpu.yml` | **신규** |
| `scripts/install.sh` | 수정 (프로파일명 + 시딩 + GPU 감지 + 시크릿 생성) |
| `.env.example` | 수정 (전면 업데이트) |
| `.env` | 수정 (Airflow secrets + 신규 설정 추가, git 미추적) |
| `docker-compose.yml` | 수정 (볼륨 경로 변수화 + Airflow 시크릿 외부화 + vLLM gpu 프로파일) |
| `.github/workflows/deploy.yml` | 수정 (`--profile gpu` 추가) |
| `prime_jennie/domain/config.py` | 수정 (embed_model/provider + universe_market) |
| `prime_jennie/services/scout/universe.py` | 수정 (market 설정화) |
| `prime_jennie/infra/llm/providers/ollama.py` | 수정 (embed_model 설정화) |
| `prime_jennie/services/news/archiver.py` | 수정 (embed_provider 분기) |
| `prime_jennie/services/scout/rag_retriever.py` | 수정 (embed_provider 분기 + qdrant_url 설정화) |
| `prime_jennie/services/jobs/app.py` | 수정 (seed endpoint 추가) |

## 검증 결과

- **Unit tests**: 596 passed, 0 failed (기존 테스트 전량 통과)
- **Ruff format/lint**: 통과
- **Docker Compose config**: base + no-gpu 모두 파싱 성공
- **볼륨 경로**: `DOCKER_DATA_DIR=./data` → 올바르게 치환 확인
- **기본값 보존**: 모든 신규 필드의 기본값이 현재 운영값과 동일 — 기존 환경 무변경

## 핵심 안전장치 (기존 로직 보호)

1. **모든 신규 config 필드에 기본값 = 현재 운영값**
   - `embed_model="nlpai-lab/KURE-v1"`, `embed_provider="vllm"`, `universe_market="KOSPI"`
2. **기존 .env에 Airflow 시크릿 추가** — compose에서 빈 값으로 시작 방지
3. **deploy.yml에 `--profile gpu` 추가** — vLLM 프로파일 분리 후에도 기존 서버 정상 동작
4. **핵심 비즈니스 로직 파일 무수정** — scanner, buyer, seller, monitor 등

## 배포 주의사항

- **장중 배포 금지** (09:00~15:30 KST) — push는 15:30 이후
- 이 커밋 배포 시 vLLM은 자동으로 `gpu` 프로파일로 분리되나, deploy.yml에 이미 반영
- 기존 .env에 Airflow 키가 추가되어 있으므로 Airflow 재시작 시에도 동일 키 사용

## Phase 3 (향후)

- Bridge 네트워크 모드 (macOS/Windows 지원)
- 시작 시 필수 설정값 검증
- GHCR 이미지 빌드 파이프라인
- KIS 모의투자 모드 가이드
