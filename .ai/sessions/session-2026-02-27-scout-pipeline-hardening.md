# Session Handoff - 2026-02-27 Scout 파이프라인 강화 + 크롤러 인프라

## 작업 요약 (What was done)

23개 커밋, 38개 파일 변경 (+1,889/-119). 크게 5개 영역:

### 1. Scout 파이프라인 대규모 개선
- **LLM 분석 결과 전량 저장**: `llm_grade` 컬럼 추가 (migration 006), 전체 후보 LLM 응답 저장
- **동전주 필터**: `market_cap` 하한 도입 → 500억(KOSPI 123종목)
- **KOSDAQ 제외**: Scout universe KOSPI 전용
- **RAG 후보에도 시총 필터 적용**: 소형주 유입 차단
- **Forward 컨센서스 반영**: Scout Quant Scorer에 FnGuide PER/ROE 적용 (migration 007 `stock_consensus` 테이블)
- **hybrid_score 기반 포지션 사이징**: 점수 구간별 weight 세분화
- **워치리스트 시총 타이브레이커**: 동점 시 시총 큰 종목 우선
- **섹터 budget 조정**: COOL cap 2→3, 전체 watchlist 20→25, 반도체/IT cap 4
- **수급 점수 조정**: 외인 비중 8→6pt, 기관 6→8pt
- **run_id 이력 보존**: `watchlist_history`/`daily_quant_scores`에 run_id 추가 (migration 008)
- **LLM provider override**: Scout trigger에 `llm_provider` 파라미터 추가
- **동시성 개선**: `poc_concurrency_test.py` 추가

### 2. Council Regime 매핑 개선
- **sentiment_score 기반** 변경: label 대신 숫자 점수 기준 (>=70 STRONG_BULL, >=55 BULL 등)
- **수급 해석 프롬프트 보정**: 외인 매도 + 국내 흡수 = 중립, 개인 순매수 + 3주체 합산 추가
- **인라인 프롬프트에 수급 상쇄 원칙 주입**

### 3. Scout 점수 평탄화
- **MA smoothing**: window=3 이동평균 (`SCOUT_MA_WINDOW`)
- **히스테리시스**: entry≥62, exit<55, 유지구간 55~62는 기존 WL 종목만 유지
- **DB에는 raw score 보존**, Redis/Dashboard에 MA score 반영
- **30일 초과 이력 자동 정리** (`SCOUT_HISTORY_RETENTION_DAYS`)

### 4. Scanner + Risk Gate 수정
- **Scanner regime 소스**: `watchlist:active` 고정값 → `macro:trading_context` 실시간
- **BEAR 매수 차단 해제**: `block_bear=False` (기존 안전장치로 보호)
- **MANUAL_SYNC 시 Redis watermark 초기화**

### 5. 크롤러 인프라 + Contract Test
- **분기 재무 갱신 Job**: `crawl_naver_fundamentals()` + DAG (분기별 15일 04:00)
- **컬럼 인덱스 오프셋 수정**: fundamentals 크롤러 날짜 행 th ↔ 데이터 행 td 인덱스 불일치 해결
- **FnGuide 컨센서스 크롤러**: `crawl_fnguide_consensus()` + Naver fallback
- **Contract Smoke Test DAG**: 매일 21:00 KST, sentinel(005930)로 5개 크롤러 검증, 실패 시 텔레그램 알림

## 현재 상태 (Current State)
- 브랜치: `development` — push 완료, clean
- 테스트: 596 unit passed, 15 contract passed (1 skipped)
- 배포: development push 시 자동 배포 (GitHub Actions)
- Airflow DAG 추가: `contract_smoke_test`, `collect_quarterly_financials`, `collect_consensus`

## 다음 할 일 (Next Steps)
- [x] **Scout 실전 모니터링**: 점수 평탄화 + 시총 필터 적용 후 WL 품질 관찰 (2~3일) ✅
- [x] **Council sentiment_score 보정**: regime 경계값(55/70) 실적 대비 검증 ✅
- [x] **FnGuide target_price 파싱 실패 조사**: contract test에서 skip됨 — HTML 구조 확인 필요 ✅
- [ ] **동시성 POC 결과 반영**: `poc_concurrency_test.py` 실행 후 본 코드 적용 여부 결정
- [ ] **컨센서스 thin coverage 개선**: 3명 미만 필터로 빠지는 종목 비율 확인

## Context for Next Session
다음 세션 시작 시 아래 파일들을 먼저 읽어주세요:
- `prime_jennie/services/scout/app.py` — Scout 메인 파이프라인 (MA smoothing, 히스테리시스)
- `prime_jennie/services/council/app.py` — regime 매핑 로직 (`_update_trading_context`)
- `prime_jennie/services/jobs/app.py:1650-` — contract smoke test endpoint
- `dags/utility_jobs_dag.py` — 전체 DAG 스케줄 확인

## 핵심 결정사항 (Key Decisions)
- **KOSDAQ 완전 제외**: 당분간 KOSPI만 — 변동성 높고 데이터 품질 낮음
- **시총 하한 500억**: 너무 높으면 종목 부족, 너무 낮으면 소형주 위험
- **수급 점수 외인 비중 축소**: 외인 매도가 과대평가되던 문제 — 기관 비중 상향으로 균형
- **Contract test를 CI가 아닌 Airflow DAG로**: job-worker가 크롤러를 직접 호출, 텔레그램 알림 활용
- **점수 평탄화 window=3**: 너무 길면 신호 지연, 짧으면 평탄화 효과 부족

## 주의사항 (Warnings)
- **장중 push 금지**: 09:00~15:30 KST 동안 git push하면 서비스 재시작 → 틱 중단
- **config.py vs .env 값 차이**: `stop_loss_pct` config 기본 6.0, 운영 .env 5.0
- **프롬프트 파일 미참조**: `.txt` 프롬프트 파일은 코드에서 미사용, 실제 프롬프트는 `pipeline.py` 인라인
- **vLLM v0.15.1 고정**: latest 사용 시 AWQ 호환 문제
- **Alembic migration 3개 추가됨**: 006 (llm_grade), 007 (stock_consensus), 008 (run_id) — 배포 시 자동 실행
