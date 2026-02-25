# Session Handoff - 2026-02-25 Dashboard 수정 (P&L + Positions + Watchlist)

## 작업 요약 (What was done)

### 1. Cumulative P&L 차트 2/18 멈춤 수정 (`00531cb`)

Dashboard Portfolio 페이지의 Cumulative P&L 차트가 2월 18일 이후 데이터가 없는 문제 수정.

**원인**: `daily_asset_snapshot()` Job이 `total_profit_loss`, `realized_profit_loss`를 계산하지 않고 NULL로 저장. 2/19 마이그레이션 이후 모든 스냅샷에 NULL.

**수정 파일 (1개):**
- `prime_jennie/services/jobs/app.py` — `daily_asset_snapshot()` 전면 개선
  - 미실현 손익: KIS 잔고의 `(current_value - total_buy_amount)` 합산
  - 실현 손익: 당일 trade_logs SELL의 `profit_amount` 합산
  - UPSERT: 기존 `session.add()` → `session.get()` 체크 후 INSERT or UPDATE (같은 날 재실행 시 PK 중복 방지)
  - 과거 NULL 데이터 backfill: SQL UPDATE로 2/19~2/24 데이터 복구

### 2. Portfolio Positions 빈 화면 수정 (`edb2c7b`)

Positions 탭이 "No positions"으로 표시되는 문제 수정.

**원인**: 프론트엔드가 `/portfolio/live` (Redis) 호출 → Monitor가 장 마감 후 publish 안 함 → Redis 키 비어있음 → 빈 배열 → "No positions"

**수정 파일 (1개):**
- `prime_jennie/services/dashboard/routers/portfolio.py` — `/portfolio/live` 엔드포인트에 3단계 fallback 체인 추가
  1. Redis (Monitor 30초 갱신) — 기존
  2. KIS API + DB 메타데이터 merge — **신규 fallback**
  3. DB only (가격 없이라도 표시) — **신규 fallback**

### 3. Watchlist History 500 에러 수정 (`edb2c7b`)

Scout 페이지의 Watchlist History (DB) 섹션이 비어있는 문제 수정.

**원인**: 마이그레이션 005 미실행 → DB에 `quant_score`, `sector_group`, `market_regime` 컬럼 없음 → SQLAlchemy SELECT에서 `Unknown column 'watchlist_histories.quant_score'` → 500 에러

**수정:**
- DB에 마이그레이션 005 수동 실행 (ALTER TABLE + alembic_version_app 004→005)
- `prime_jennie/services/dashboard/routers/watchlist.py` — API 응답에 `quant_score`, `sector_group`, `market_regime` 3개 컬럼 추가
- `frontend/src/lib/api.ts` — `WatchlistEntry` 타입에 3개 필드 추가
- `frontend/src/pages/Scout.tsx` — Watchlist History 테이블에 Sector, Quant Score 컬럼 표시

### 4. 테스트 수정 (`1cd9891`, `80da836`)
- `test_scout_db_save.py` — 미사용 `pytest` import 제거 (ruff lint)
- `test_telegram.py` — `test_sellall_with_confirmation` mock 포지션 누락 수정

## 현재 상태 (Current State)
- development 브랜치, push 완료 (`edb2c7b`)
- **CI 전체 통과**: lint, unit tests, e2e tests, frontend build
- **배포 완료**: dashboard + dashboard-frontend (docker compose `--no-deps`)
- DB: alembic version `005`, watchlist_histories 12개 컬럼 정상

## 검증 결과
- `/api/portfolio/live`: 5개 포지션 + 실시간 가격 정상 반환 (KIS fallback 동작 확인)
- `/api/watchlist/history`: 20건 정상 반환 (quant_score, sector_group, market_regime 포함)
- `/api/portfolio/history`: P&L 데이터 정상 표시

## 다음 할 일 (Next Steps)
- [ ] Scout 실행 후 RAG 뉴스 프롬프트 주입 효과 확인 (LLM 분석 quality 변화)
- [ ] LLM Usage 대시보드 정상 집계 확인 (다음 Scout 실행 후)
- [ ] Qdrant payload index 추가 (`metadata.created_at_utc`, `metadata.stock_code`)
- [ ] 월간 ROE 갱신 Job 구현 (TODO #1)
- [ ] 방산 대형주 스코어링 개선 (TODO #3)

## Context for Next Session
- `.ai/sessions/session-2026-02-25-dashboard-fixes.md` — 이 핸드오프 파일
- `.ai/sessions/session-2026-02-25-rag-and-llm-usage.md` — 이전 세션 (Scout RAG + LLM Usage)
- `.ai/TODO.md` — 전체 TODO 목록
- `prime_jennie/services/dashboard/routers/portfolio.py` — positions fallback 체인
- `prime_jennie/services/jobs/app.py` — daily_asset_snapshot P&L 계산

## 핵심 결정사항 (Key Decisions)
- **Positions 3단계 fallback**: Redis → KIS API → DB only. 어떤 상황에서든 보유 종목이 보이도록
- **마이그레이션 수동 실행**: 컨테이너 내 Python으로 ALTER TABLE + alembic_version UPDATE (로컬 환경에 DB 인증 없음)
- **Watchlist API 컬럼 확장**: 기존 9개 → 12개 (quant_score, sector_group, market_regime 추가)
- **Scout UI 컬럼 추가**: Sector, Quant Score 표시 (다음 Scout 실행부터 값 채워짐)

## 오늘 전체 세션 커밋 요약 (2026-02-25)
```
edb2c7b fix: Dashboard positions 빈 화면 + watchlist history 500 에러 수정
00531cb fix: daily-asset-snapshot에 P&L 계산 추가 + UPSERT 처리
80da836 fix: test_sellall_with_confirmation mock 포지션 누락 수정
1cd9891 fix: test_scout_db_save.py 미사용 pytest import 제거
236106c fix: LLM generate_json() 토큰 사용량 기록 누락 수정
bd15d46 feat: Scout RAG 뉴스 검색 통합
593c7ed feat: watchlist_histories에 quant_score, sector_group, market_regime 컬럼 추가
792ac8f feat: Daily Briefing 전면 개선
f273889 feat: 실시간 포지션 모니터링 + watermark DB 동기화
61f32db fix: 매매 안정성 개선 (conviction 비활성화, 매도 직렬화)
```
