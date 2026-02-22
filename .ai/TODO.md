# TODO — 미해결 과제

## 긴급 (다음 세션 우선)

### 1. ROE 일일 갱신 Job 추가
- **현상**: stock_fundamentals에 ROE를 쓰는 정기 Job이 없음
- **위험**: 레거시 scout.py Phase 1.7이 매일 ROE=None으로 덮어쓸 수 있음
- **해결안**:
  - (A) prime-jennie job-worker에 주간 ROE 수집 엔드포인트 추가 (네이버 금융 크롤링)
  - (B) 또는 daily update 시 ROE가 NULL이면 기존값 유지하도록 수정
  - (C) 레거시 scout 컨테이너 확인 → Phase 1.7 ROE=None 덮어쓰기 차단
- **참고**: 2026-02-22 수동 backfill로 184종목 ROE 100% 채움 (네이버 금융 기준)

### 2. FINANCIAL_METRICS_QUARTERLY 정기 갱신
- 레거시 `collect_quarterly_financials.py`를 prime-jennie Job으로 이식
- 분기 실적 발표 후 자동 갱신 (매 분기 1회, 4/5/7/8/10/11월)
- 현재 최신 데이터: 2025-09-30 (Q3)

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

### 5. 레거시 컨테이너 정리
- my-prime-jennie가 아직 같은 DB에 쓰고 있는지 확인
- Phase 1.7 ROE=None 덮어쓰기 문제 원인
- 레거시 컨테이너 중지 또는 DB 쓰기 권한 제거 검토

### 6. Quant Scorer Shadow Comparison 정리
- shadow log가 v2.0 vs v2.1 비교만 함 (quality delta 미추적)
- ROE 보정/PBR·PER 하한선 변경 등 v2.2 변경사항 shadow에 반영 또는 제거
