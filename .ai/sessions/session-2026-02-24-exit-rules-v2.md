# Session Handoff - 2026-02-24 (Exit Rules v2 + 포트폴리오 강화 일괄 커밋)

## 작업 요약 (What was done)

### 1. Exit Rules v2 — RSI trailing 스킵 + Death Cross BULL 비활성화 (`290fdd9`)

DB 실증 기반 exit rules 최적화 두 번째 이터레이션 (Scale-Out L0 스킵 이후).

**변경 1: RSI Overbought — Trailing TP 활성 시 비활성화**
- `check_rsi_overbought()`: `high_profit_pct >= trailing_activation_pct`이면 RSI 매도 스킵
- 근거: RSI 매도 7건 평균 +4.3%이지만 후속 매도 +8~15% → 조기 익절 손실
- Trailing이 이미 포지션을 관리하므로 RSI 50% 부분 매도 불필요

**변경 2: Death Cross — BULL/STRONG_BULL 비활성화**
- `check_death_cross(ctx, regime)`: regime 파라미터 추가
- `death_cross_bear_only: bool = True` config 추가 (SellConfig)
- BULL에서는 ATR Stop + Breakeven이 하락을 커버, SIDEWAYS/BEAR에서만 유의미

**변경 파일:**
- `prime_jennie/domain/config.py` — `death_cross_bear_only` 필드 추가
- `prime_jennie/services/monitor/exit_rules.py` — RSI trailing 체크 + death cross regime 파라미터
- `tests/unit/services/test_exit_rules.py` — 테스트 4건 추가

### 2. 상관관계 체크 + 쿨다운 시스템 + 포트폴리오 가드 강화 (`06b7f74`)

이전 세션(들)에서 작업된 미커밋 변경사항 일괄 커밋.

**Correlation Check:**
- `prime_jennie/services/buyer/correlation.py` (신규) — 보유 종목과 상관관계 0.85 이상 시 매수 차단
- `tests/unit/services/test_correlation.py` (신규)
- buyer executor에 correlation check 통합

**Cooldown 시스템:**
- Scanner: `check_stoploss_cooldown()`, `check_sell_cooldown()` 게이트 추가 (Redis 기반)
- Seller: `DEATH_CROSS`, `BREAKEVEN_STOP`도 cooldown 대상 추가 + 모든 매도 후 24h 쿨다운
- Buyer: 매수 시 이전 Redis 상태 초기화, 실제 체결가 조회 보정

**Portfolio Guard 강화:**
- `check_sector_value_concentration()` — 섹터 금액 비중 상한 30% (STRONG_BULL 50%)
- `check_stock_value_concentration()` — 종목 금액 비중 상한 15% (STRONG_BULL 25%)
- 관련 테스트 추가 (test_portfolio_guard.py, test_risk_gates.py)

**Scanner 전략 조정:**
- Golden Cross: SIDEWAYS에서도 허용 (기존 BULL/STRONG_BULL만)
- Momentum Continuation: BULL 전용으로 분리

### 3. .gitignore 업데이트 (`de79ede`)
- `*.tsbuildinfo` 패턴 추가

### 4. KIS 계좌 ↔ DB 포지션 동기화 유틸리티 (`b97220b`)

BUY/SELL 체결 시 DB positions 자동 갱신되지만, persist 실패·수동 거래 등 비상 시 불일치 해소 도구.

**Job 엔드포인트 — `/jobs/sync-positions`:**
- `compare_positions()`: KIS 보유 목록 vs DB 포지션 5-way 비교 (only_in_kis / only_in_db / quantity_mismatch / price_mismatch / matched)
- `apply_sync()`: 비교 결과 DB 반영 (INSERT/DELETE/UPDATE)
- `dry_run=True` (기본) → 리포트만 반환, `dry_run=False` → 실제 적용 + commit
- UPDATE 시 `sector_group`, `high_watermark` 보존
- 신규 INSERT 시 `high_watermark = current_price` (0이면 avg fallback)

**CLI 스크립트 — `scripts/sync_positions.py`:**
- `--dry-run` (기본), `--apply`, `--auto-confirm` 플래그
- 카테고리별 콘솔 리포트 출력
- `run_backtest.py` 패턴 (dotenv + get_engine + Session)

**변경 파일:**
- `prime_jennie/services/jobs/app.py` — `compare_positions()`, `apply_sync()`, `/jobs/sync-positions` 엔드포인트 추가
- `scripts/sync_positions.py` (신규) — CLI 동기화 스크립트
- `tests/unit/services/test_sync_positions.py` (신규) — 16건 단위 테스트

## 커밋 이력
1. `290fdd9` — `feat: exit-rules-v2 — RSI trailing 스킵 + Death Cross BULL 비활성화`
2. `06b7f74` — `feat: 상관관계 체크 + 쿨다운 시스템 + 포트폴리오 가드 강화`
3. `de79ede` — `chore: .gitignore에 *.tsbuildinfo 추가`
4. `b97220b` — `feat: KIS 계좌 ↔ DB 포지션 동기화 유틸리티`

## 현재 상태 (Current State)
- development 브랜치, 594 tests passed, ruff clean
- Working tree clean (모든 변경 커밋 + push 완료)
- GitHub Actions 배포 트리거됨

### Exit Rules 우선순위 체인 (최신)
0. Hard Stop (-10%) → 1. Profit Floor → 2. Profit Lock → 2.5 Breakeven Stop → 3. ATR Stop → 4. Fixed Stop → 5. Trailing TP → 6. Scale-Out → 7. RSI Overbought (**trailing 활성 시 스킵**) → 8. Target → 9. Death Cross (**BULL 비활성화**) → 10. Time Exit

## 다음 할 일 (Next Steps)
- [ ] Exit rules v2 성과 모니터링 (RSI 스킵/Death Cross 스킵 건수 추적)
- [ ] `.ai/sessions/session-2026-02-22-exit-rules-v2-cost-optimization.md` 남은 이터레이션 검토
- [ ] TODO.md 미해결 과제 (ROE 정기 갱신 Job, 분기 재무제표 갱신 등)
- [ ] sync-positions 실 환경 검증 (`/jobs/sync-positions?dry_run=true` 호출)

## Context for Next Session
다음 세션 시작 시 아래 파일들을 먼저 읽어주세요:
- `prime_jennie/services/monitor/exit_rules.py` — 현재 exit rules 전체 로직
- `prime_jennie/services/buyer/correlation.py` — 상관관계 체크 로직
- `prime_jennie/services/buyer/portfolio_guard.py` — 포트폴리오 가드 (섹터/종목 비중 포함)
- `prime_jennie/services/jobs/app.py` — sync-positions 엔드포인트 (compare_positions, apply_sync)
- `.ai/TODO.md` — 미해결 과제 목록

## 핵심 결정사항 (Key Decisions)
- **RSI 비활성화 방식**: 완전 제거가 아닌 조건부 스킵 (trailing 비활성 시 여전히 RSI 매도 가능)
- **Death Cross config**: `death_cross_bear_only` 기본 True (즉시 적용, env 오버라이드 가능)
- **별도 config 필드 최소화**: RSI는 기존 `trailing_enabled` + `trailing_activation_pct` 조합으로 판단
- **sync-positions dry_run 기본**: 안전을 위해 기본 dry_run=True, 명시적으로 false 전달 시만 적용

## 주의사항 (Warnings)
- `config.py` 기본값(`stop_loss_pct=6.0`)과 `.env` 운영값(`5.0`)이 다름에 주의
- correlation check는 KIS API `get_daily_prices` 호출 — 매수 시 추가 API 콜 발생
- sync-positions INSERT 시 `sector_group=None` — 이후 update-naver-sectors Job으로 채워야 함
