# TODO — 미해결 과제

## 긴급 (다음 세션 우선)

### 0. 재부팅 후 전체 서비스 상태 점검 (별도 창에서 진행 중)
- [ ] 전체 서비스 정상 기동: `docker compose --profile infra --profile real ps`
- [ ] Monitor/Scanner tick consumer Redis 연결 확인 (BusyLoadingError 없이 시작)
- [ ] **Gateway WebSocket 연결 유지 확인** — PINGPONG 수정 후 ~10초 끊김 해소 여부
  - `docker logs prime-jennie-kis-gateway-1 | grep -i "pingpong\|closed\|connected"`
- [ ] buy-scanner / price-monitor tick 수신 확인 (장중 tick count 증가 로그)
- [ ] Airflow DAG `/start`(09:00), `/stop`(15:30) 정상 호출 확인
- [ ] Loki 로그 공백 없이 정상 수집 (Grafana에서 09:00~15:30 연속 확인)
- [ ] 장중 매수/매도 정상 발생 여부 (매수/매도 건수 확인)
- **참고**: `session-2026-02-23-race-condition-fix.md`, `session-2026-02-23-websocket-pingpong-fix.md`

### 1. ROE 정기 갱신 Job 추가
- **현상**: stock_fundamentals에 ROE를 쓰는 정기 Job이 없음 (레거시 중단됨)
- **해결안**: prime-jennie job-worker에 월간 ROE 수집 엔드포인트 추가 (네이버 금융 크롤링)
- **참고**: 2026-02-22 수동 backfill로 184종목 ROE 100% 채움 (네이버 금융 기준)
- 분기 재무제표 기반이라 월 1회 갱신이면 충분

### 2. FINANCIAL_METRICS_QUARTERLY 정기 갱신
- 레거시 `collect_quarterly_financials.py`를 prime-jennie Job으로 이식
- 분기 실적 발표 후 자동 갱신 (매 분기 1회, 4/5/7/8/10/11월)
- 현재 최신 데이터: 2025-09-30 (Q3)

### 6. 명시적 장 오픈 시간 체크 추가
- **현상**: buy-scanner, price-monitor, executor 어디에도 "장이 열려있는지" 체크가 없음
- **현재 안전장치**: KIS WebSocket이 장외에 틱을 안 보내서 사실상 동작 안 함 (암묵적 의존)
- **위험**: 어떤 이유로 장외에 틱이 발생하면 매수/매도 주문이 실행될 수 있음
- **해결안**: scanner risk_gates에 장 오픈 시간(09:00~15:30) 체크 gate 추가, executor에도 주문 전 시간 검증
- **참고**: Gateway `/api/market/is-market-open` 엔드포인트는 이미 존재 (다른 서비스에서 호출 안 함)
- **참고**: `session-2026-02-23-websocket-pingpong-fix.md`

### 7. /watch, /unwatch 커맨드 실효성 확보
- **현상**: `watchlist:manual` Redis hash에 기록하지만 Scanner가 읽지 않음 → 효과 없음
- **해결안**: Scanner watchlist 로드 시 `watchlist:manual` 병합, 또는 커맨드 제거
- **참고**: `session-2026-02-23-telegram-fix.md`

### 8. ~~watchlist_histories DB 기록 프로세스 추가~~ ✅ 완료
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
