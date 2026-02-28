# Session Handoff - 2026-02-25 ORB Strategy

## 작업 요약 (What was done)

### Part 1: ORB (Opening Range Breakout) 전략 구현
- `SignalType.ORB_BREAKOUT` enum 추가 (MOMENTUM_STRATEGIES에는 미포함 — 시장가 주문)
- `ScannerConfig`에 ORB 설정 6개 필드 추가 (`orb_enabled=False` 기본)
- `detect_orb_breakout()` 전략 함수 구현 (OR 상단 돌파 + 거래량 + range 폭 + 시간대 체크)
- `BuyScanner.process_tick()`에 ORB 통합: `_opening_ranges` dict, `_update_opening_range()`, partial gate bypass
- `docker-compose.yml`에 `SCANNER_ORB_ENABLED: "false"` 추가
- 9개 ORB 단위 테스트 추가 (총 558 unit tests 통과)

### Part 2: 기존 전략 퀀트 적합성 감사 (보고만)
- 7개 전략 감사 → DIP_BUY(높음), MOMENTUM(높음) 과적합 위험
- TODO #9로 파라미터 튜닝 항목 추가

### 변경 파일 (7개)
- `prime_jennie/domain/enums.py` — ORB_BREAKOUT enum
- `prime_jennie/domain/config.py` — ORB config 필드
- `prime_jennie/services/scanner/strategies.py` — detect_orb_breakout()
- `prime_jennie/services/scanner/app.py` — BuyScanner ORB 통합
- `docker-compose.yml` — SCANNER_ORB_ENABLED env
- `tests/unit/services/test_strategies.py` — 9개 ORB 테스트
- `.ai/TODO.md` — 파라미터 튜닝 TODO 추가

## 현재 상태 (Current State)
- ORB 전략은 `orb_enabled=False`로 배포됨 (수동 활성화 필요)
- 기존 전략 전부 정상 동작 (558 unit tests 통과)
- development 브랜치, main에 머지 완료

## 다음 할 일 (Next Steps)
- [ ] ORB 활성화 후 장중 시그널 모니터링 (docker-compose에서 `"true"` 전환)
- [ ] DIP_BUY / MOMENTUM 파라미터 튜닝 (1-2주 운영 데이터 기반, TODO #9)
- [x] FINANCIAL_METRICS_QUARTERLY 정기 갱신 Job (TODO #2) ✅

## Context for Next Session
다음 세션 시작 시 아래 파일들을 먼저 읽어주세요:
- `prime_jennie/services/scanner/strategies.py` — ORB 전략 로직
- `prime_jennie/services/scanner/app.py` — ORB 통합 (process_tick)
- `.ai/TODO.md` — 미해결 과제 목록

## 핵심 결정사항 (Key Decisions)
- ORB_BREAKOUT을 MOMENTUM_STRATEGIES에서 **제외** — breakout은 시장가 즉시 체결이 적합 (지정가 미체결 위험)
- ORB는 Conviction과 동일한 partial gate bypass 패턴 — RSI guard / combined_risk 스킵 (장 초반 모멘텀 종목은 본질적으로 RSI 높음)
- `no_trade_window(09:00-09:15)` = opening range 수집 시간으로 자연 정합
- 기본 비활성 (`orb_enabled=False`) — 검증 후 수동 활성화

## 주의사항 (Warnings)
- ORB 활성화 시 장 초반(09:15-10:30) 매수 빈도 증가 예상 — max_buy_count_per_day(6) 한도 주의
- backtest/daily_strategies.py에 ORB 미포함 (일봉 백테스트에 opening range 개념 부적합)
