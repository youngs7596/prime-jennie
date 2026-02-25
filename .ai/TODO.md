# TODO — 미해결 과제

## 긴급 (다음 세션 우선)

### ~~0. 재부팅 후 전체 서비스 상태 점검~~ ✅ 완료
- 2026-02-23 세션에서 점검 완료

### ~~1. ROE 정기 갱신 Job 추가~~ ✅ 완료
- `crawl_naver_roe()` + `/jobs/collect-naver-roe` 엔드포인트 + 월간 DAG (`0 3 1 * *`)

### 2. FINANCIAL_METRICS_QUARTERLY 정기 갱신
- 레거시 `collect_quarterly_financials.py`를 prime-jennie Job으로 이식
- 분기 실적 발표 후 자동 갱신 (매 분기 1회, 4/5/7/8/10/11월)
- 현재 최신 데이터: 2025-09-30 (Q3)

### ~~6. 명시적 장 오픈 시간 체크 추가~~ ✅ 완료
- buyer/seller executor `process_signal()`에 KST 09:00~15:30 체크 추가
- MANUAL 매도는 시간 체크 우회

### ~~7. /watch, /unwatch 커맨드 실효성 확보~~ ✅ 완료
- Scanner `load_watchlist()`에서 `watchlist:manual` Redis hash 병합
- manual 종목은 최소 스코어(50)로 watchlist에 추가

### ~~8. watchlist_histories DB 기록 프로세스 추가~~ ✅ 완료
- Phase 8 DB 저장 구현 (커밋 `08f68b7`) + 컬럼 보강 (quant_score, sector_group, market_regime)
- Alembic migration 005 추가

## 중요 (성능 개선)

### 3. 방산 대형주 스코어링 개선
- **현상**: 한화에어로(Q=62), 현대로템(Q=58), HD현대중공업(Q=57) — watchlist 미진입
- **원인**: PBR 8-11, PER 25-82 → Quality+Value 합산 낮음 (ROE 높아도 한계)
- **해결안 후보**:
  - (A) 섹터별 PBR/PER 상대평가 (같은 섹터 내 백분위)
  - (B) 조선/방산 등 테마 모멘텀 가산점 (뉴스 감성 + 섹터 모멘텀 연동)
  - (C) 시가총액 상위 N개 종목 자동 포함 (universe guarantee)
- **주의**: 한번에 너무 많이 바꾸지 않기 (효과 측정 분리)

### 4. E2E Mock KIS Gateway 테스트 구축
- 계획 완료: `.claude/plans/memoized-splashing-toucan.md`
- Mock Gateway + BuyExecutor/SellExecutor E2E 테스트
- fakeredis + SQLite in-memory 기반
- 매수 8건 + 매도 8건 + 라운드트립 3건 = 총 19개 테스트

## 개선 (여유 시 진행)

### 5. Quant Scorer Shadow Comparison 정리
- shadow log가 v2.0 vs v2.1 비교만 함 (quality delta 미추적)
- ROE 보정/PBR·PER 하한선 변경 등 v2.2 변경사항 shadow에 반영 또는 제거
