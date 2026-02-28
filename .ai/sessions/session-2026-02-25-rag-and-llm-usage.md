# Session Handoff - 2026-02-25 Scout RAG 통합 + LLM Usage 수정

## 작업 요약 (What was done)

### 1. Scout RAG 뉴스 검색 통합 (`bd15d46`)

my-prime-jennie에서 마이그레이션 시 누락된 Qdrant RAG 뉴스 검색을 Scout 파이프라인에 통합.

**변경 파일 (6개):**
- `prime_jennie/services/news/archiver.py` — `stock_name`, `created_at_utc` 메타데이터 추가
- `prime_jennie/services/scout/rag_retriever.py` — **신규** RAG 검색 모듈
  - `init_vectorstore()`: Qdrant + KURE-v1 임베딩 초기화 (config 체크, graceful fallback)
  - `discover_rag_candidates()`: 4개 토픽 쿼리로 유망 종목 탐색 (7일 필터, k=20)
  - `fetch_news_for_stocks()`: 종목별 3+1(섹터) 쿼리, 8 workers 병렬, 최대 5건/150자
- `prime_jennie/services/scout/enrichment.py` — `rag_news_context: str | None` 필드 추가
- `prime_jennie/services/scout/analyst.py` — `_build_prompt()`에 `### 최근 뉴스 (RAG)` 섹션 주입
  - skip 플레이스홀더: "뉴스 DB 미연결", "최근 관련 뉴스 없음", "뉴스 검색 오류"
- `prime_jennie/services/scout/app.py` — 파이프라인 Phase 1.5 + Phase 2.5 추가
  - Phase 1.5: RAG 후보 발굴 → DB에서 StockMaster 조회 후 universe merge
  - Phase 2.5: RAG 뉴스 프리페치 → enriched에 rag_news_context 주입
- `tests/unit/services/test_scout_rag.py` — **신규** 10개 테스트

**파이프라인 흐름 (변경 후):**
```
Phase 1   (10%) → Universe Loading
Phase 1.5 (15%) → RAG 후보 발굴 (신규)
Phase 2   (25%) → Enrichment
Phase 2.5       → 섹터 모멘텀 + RAG 뉴스 프리페치 (신규)
Phase 3   (45%) → Quant Scoring
Phase 4   (60%) → LLM Analysis (프롬프트에 뉴스 자동 포함)
Phase 5-8       → Selection, Save
```

### 2. LLM 토큰 사용량 기록 누락 수정 (`236106c`)

대시보드에 DeepSeek R1 토큰이 ~70K만 표시 (실제 ~3M). 원인: `generate_json()`에서 `_record_usage()` 누락.

**수정 파일 (3개):**
- `prime_jennie/infra/llm/providers/openai_provider.py` — `generate_json()`에 토큰 추출 + `_record_usage()` 추가
- `prime_jennie/infra/llm/providers/gemini.py` — `generate()` + `generate_json()` 모두 `_record_usage()` 추가
- `prime_jennie/infra/llm/providers/claude.py` — `generate_json_with_thinking()`에 `_record_usage()` 추가

**수정 전/후 매트릭스:**

| Provider | `generate()` | `generate_json()` | 기타 |
|----------|:-----------:|:------------------:|:----:|
| openai (DeepSeek) | 정상 | **수정 (누락→추가)** | — |
| ollama (vLLM) | 정상 | 정상 (generate 위임) | — |
| claude | 정상 | 정상 (generate 위임) | **thinking 수정** |
| gemini | **수정 (누락→추가)** | **수정 (누락→추가)** | — |

### 3. 테스트 수정 (`1cd9891`, `80da836`)
- `test_scout_db_save.py` — 미사용 `pytest` import 제거 (ruff lint)
- `test_telegram.py` — `test_sellall_with_confirmation` mock 포지션 누락 수정
  - MagicMock이 빈 iterable 반환 → xadd 미호출. 실제 포지션 1개 mock 추가.

## 현재 상태 (Current State)
- development 브랜치, push 완료 (`80da836`)
- **CI 전체 통과**: lint, unit tests, e2e tests, frontend build, deploy
- **배포 완료**: scout-job, news-pipeline, macro-council, daily-briefing, dashboard (docker compose 개별 배포 + GitHub Actions 전체 배포)
- Qdrant: 36,553 points, `stock_name` + `created_at_utc` 메타데이터 정상 저장 확인

## 다음 할 일 (Next Steps)
- [x] Scout 실행 후 RAG 뉴스 프롬프트 주입 효과 확인 (LLM 분석 quality 변화) ✅
- [x] LLM Usage 대시보드 정상 집계 확인 (다음 Scout 실행 후) ✅
- [x] Qdrant `metadata.created_at_utc`, `metadata.stock_code`에 payload index 추가 (필터 성능 개선) ✅
- [x] 월간 ROE 갱신 Job 구현 (TODO #1) ✅
- [x] 방산 대형주 스코어링 개선 (TODO #3) ✅

## Context for Next Session
- `.ai/sessions/session-2026-02-25-rag-and-llm-usage.md` — 이 핸드오프 파일
- `.ai/TODO.md` — 전체 TODO 목록
- `prime_jennie/services/scout/rag_retriever.py` — RAG 검색 모듈 (신규)
- `prime_jennie/infra/llm/base.py:28-44` — `_record_usage()` 공통 메서드

## 핵심 결정사항 (Key Decisions)
- **RAG graceful degradation**: vectorstore 초기화 실패/비활성 시 None 반환, 파이프라인 중단 없이 RAG 없이 진행
- **RAG 후보 → universe merge**: DB에서 StockMaster 조회 후 is_active=True인 종목만 추가
- **뉴스 skip 플레이스홀더**: 의미 없는 텍스트("뉴스 DB 미연결" 등)는 LLM 프롬프트에서 제외
- **장중 배포**: `--no-deps`로 개별 서비스만 재시작 (kis-gateway 미터치), 장 마감 후 git push
