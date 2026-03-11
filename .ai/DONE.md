# DONE — 완료된 과제 아카이브

> TODO.md에서 완료 확인 후 이동된 항목들

### 0. 재부팅 후 전체 서비스 상태 점검 — ✅ 2026-02-23
### 1. ROE 정기 갱신 Job 추가 — ✅ 2026-02-23
### 2. FINANCIAL_METRICS_QUARTERLY 정기 갱신 — ✅ 2026-02-23
### 6. 명시적 장 오픈 시간 체크 추가 — ✅ 2026-02-24
### 7. /watch, /unwatch 커맨드 실효성 확보 — ✅ 2026-02-25
### 8. watchlist_histories DB 기록 프로세스 — ✅ 2026-02-25
### 10. News Pipeline 구조 개선 (병렬 스레드) — ✅ 2026-03-05
- 3개 독립 스레드, NEWS_STREAM_MAXLEN 10,000

### (세션 발견) WebSocket backoff 리셋 — ✅ 2026-03-06
### (세션 발견) Docker 미사용 이미지 정리 (59GB) — ✅ 2026-03-06
### (세션 발견) Telegram EOFError 처리 — ✅ 2026-03-06
### (세션 발견) Dashboard/Backtest regime 매핑 통일 — ✅ 2026-03-04
### (세션 발견) Circuit breaker → Intraday Risk Throttle — ✅ 2026-03-06
### (세션 발견) Council Claude Opus Chief Judge 전환 — ✅ 2026-03-06
### (세션 발견) signal_logs 테이블 (백테스트 이력) — ✅ 2026-03-06
### (세션 발견) 강제 청산 기능 구현 — ✅ 2026-03-01
### (세션 발견) MarketCalendar 통합 체크 — ✅ 2026-03-07
### (세션 발견) WSJ 뉴스레터 파이프라인 구축 — ✅ 2026-03-07
### #13 감성 분석 실제 동작 확인 — ✅ 2026-03-08
- 95%+ score≠50, 평균 57.8 확인
### RSI_REBOUND 전략 비활성화 — ✅ 2026-03-09
- 실전 승률 30%, 평균 PnL -1.16%, 백테스트 스윕으로 확인
- scanner, backtest, council prompt에서 비활성화
### 데일리 브리핑 중복 발송 수정 — ✅ 2026-03-09
- Redis 멱등성 체크 (`briefing:sent:{date}`)
### LLM 프로바이더 정비 (EXAONE → 뉴스 전용) — ✅ 2026-03-09
- 브리핑/WSJ 요약/Council Chief Judge → Claude Opus 4.6 전환
- 주의: DONE.md 기존 "Council Claude Opus Chief Judge 전환 ✅ 2026-03-06"은 실제 미완성이었음, 이번에 실수정
### #22 GHCR deploy CI 타이밍 레이스 — ✅ 2026-03-09
- `branch=development` 필터 추가하여 해당 브랜치 CI만 확인
### #23 daily_briefing_report execution_timeout 조정 — ✅ 2026-03-10
- timeout 5분→10분, retries 2→1 (멱등성 보호 있어 재시도 축소)
### #15 macro_quick Naver API rate limit 확인 — ✅ 2026-03-10
- 5분당 Naver 호출 4건 (0.8 req/min), max_active_runs=1 — 별도 throttle 불필요
### #11 WSJ 자동 파이프라인 동작 확인 — ✅ 2026-03-11
- 07:50 DAG 정상, What's News body fallback 구현+테스트, 텔레그램 Claude 한글요약 fire-and-forget, 실패 시 Council graceful degradation
### #12 MarketCalendar gating 확인 — ✅ 2026-03-11
- Scanner/Monitor 백그라운드 스레드에서 is_market_open() 자동 gating (09:00 활성화, 15:30 중지), Gateway 거래일 API 연동, 162개 단위 테스트
### #14 trading_flags:stop 해제 판단 — ✅ 2026-03-11
- stop=1 중 Scanner가 Stream 발행 차단 (DB 로그만), 해제 시 새 시그널부터 처리, Executor에서 stop 이중 확인 — 기술적 안전 확인
### #19 텔레그램 WSJ 요약 프롬프트 튜닝 — ✅ 2026-03-11
- 현재 품질 충분, 추가 튜닝 불필요로 판단
### #20 VKOSPI 데이터 소스 확보 — ✅ 2026-03-11
- 무료 API로 VKOSPI 안정적 수집 불가 (KRX, Naver, Yahoo 모두 실패), US VIX(Yahoo Finance) 유지 결정
