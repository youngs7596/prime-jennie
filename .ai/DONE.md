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
### RSI_REBOUND 전략 코드 완전 삭제 — ✅ 2026-03-11
- detect_rsi_rebound() 함수 삭제, _check_rsi_rebound() 삭제
- GAP_UP_REBOUND 전략으로 대체 (역추세 반등 포착)
### #5 Shadow Comparison 삭제 — ✅ 2026-03-11
- scout/quant.py _log_shadow_comparison() 100줄 삭제
### GAP_UP_REBOUND 전략 v1 구현 — ✅ 2026-03-11
- 조건: 전일대비 +2% 갭업 + 거래량 1.5x + 시가 유지
- Scanner: Gateway API에서 전일종가/시가 캐시, partial gate bypass (rsi_guard, micro_timing, market_regime 스킵)
- Backtest: 전일 -3%+ 후 갭업 +2%+, 거래량 수반, 양봉 확인
- 테스트 5건 추가 (전체 877 passed)
### Overextension Filter (Gate 11) 구현 + Grid Search 최적화 — ✅ 2026-03-11
- 60일 이격률 기반 과열 매수 차단, 국면별 임계값: STRONG_BULL 35%, BULL 30%, SIDEWAYS 28%, BEAR 25%, STRONG_BEAR 20%
- 3-Phase Grid Search (독립 스윕 → 2,800 조합 → 미세 조정), 차단 하락률 64%
- 핵심 교훈: "AVOID 평균 ≠ 임계값 경계" — 데이터마이닝 평균(13-21%)을 직접 쓰면 과차단
- BacktestConfig에 overextension_thresholds 파라미터 추가
- risk_gates.py 테스트 9건 업데이트 (전체 877 passed)
- 보고서: `.ai/reports/overextension-grid-search-2026-03-11.md`
### #21 dev 환경 서비스 로컬 실행 테스트 — ✅ 2026-03-22
- WSL2에서 .env.dev로 MS-01 인프라 원격 연결 + 서비스 로컬 기동 검증
- DB(jennie_db_dev 2317종목)/Redis(DB 1, stop=1,dryrun=1) 연결 OK
- Scanner uvicorn 기동→graceful shutdown 정상, Buyer/Seller/Monitor/Jobs 임포트 OK
### #4 E2E Mock KIS Gateway 테스트 구축 — ✅ 2026-03-22
- MockTransport 기반 12 endpoints, GatewayState로 시나리오 제어
- 47 테스트: buy_flow 8, sell_flow 8, order_confirmation 7, full_cycle 3, pipeline_flow 21
- fakeredis + SQLite in-memory, 부분 체결/슬리피지/쿨다운 등 풀 커버
### #17 폭락장(03-03) 사후분석 — ✅ 2026-03-22
- 당일 실현손실 -2,067,164원 (매도 6건), 최대 낙폭 208.7M→176.7M (-15.3%, 03-04)
- 핵심 원인: MOMENTUM 신규매수 6건(59M) → 현금 소진 → 추가 하락 전량 노출
- Fix 1 false negative: 대한항공/한국전력 2건 체결 미기록 → MANUAL_SYNC 해소, P&L 복원 불가
- 4건 버그(체결확인/전략회피/regime기준/stop키) 모두 03-03 당일 수정 배포 완료
### #3 방산 대형주 스코어링 개선 — ✅ 2026-03-13
- PBR/PER 절대 임계값 → 섹터 내 백분위 기반 상대평가로 전환
- Quality PBR/PER (0-5), Value PER (0-10), Value PBR (0-5) 4곳 적용
- 섹터 5종목 미만 시 기존 절대평가 폴백 유지
- 방산주 시나리오 Quality+Value 합산 +12.5pt 개선
