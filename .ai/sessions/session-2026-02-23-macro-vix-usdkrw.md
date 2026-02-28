# Session Handoff - 2026-02-23 (VIX + USD/KRW 매크로 수집 추가)

## 작업 요약 (What was done)

### VIX + USD/KRW 매크로 데이터 수집 구현
macro council이 VIX와 USD/KRW 데이터를 사용하도록 모델/DB/LLM 프롬프트가 이미 완비되어 있었으나,
실제 수집 코드가 없어 항상 `None`으로 표시되던 문제를 해결.

**변경 파일:**
- `prime_jennie/domain/config.py` — `SecretsConfig`에 `bok_ecos_api_key` 필드 추가
- `prime_jennie/services/jobs/app.py` — `_fetch_vix()`, `_fetch_usd_krw()` 헬퍼 함수 추가 + `macro_collect_global()`에 통합
- `.env` — `BOK_ECOS_API_KEY` 추가 (레거시 `secrets.json`에서 이전)

**데이터 소스:**
- **VIX**: Yahoo Finance HTTP API (`^VIX` chart 엔드포인트, API 키 불필요)
  - range=5d, interval=1d → 마지막 유효 종가 사용
  - Regime 분류: <15 low_vol, 15-25 normal, 25-35 elevated, >=35 crisis
- **USD/KRW**: BOK ECOS API (한국은행 공식)
  - stat_code `731Y001`, item_code `0000001` (미 달러)
  - 10일 범위 조회 → 최신 row의 DATA_VALUE

**검증 결과 (실제 수집):**
- VIX = 20.48 (regime: normal)
- USD/KRW = 1,448.7
- Redis snapshot에 `vix`, `vix_regime`, `usd_krw` 필드 정상 저장
- macro council 트리거 성공 → insight에 VIX/USD/KRW 정상 반영

### Macro Council 실행 결과
```
Sentiment: neutral (score: 53)
Regime hint: 6,000pt 도전 국면, 수급 불균형 주의
VIX: 20.48 (normal)
USD/KRW: 1,448.7
KOSPI: 5,846.09 / KOSDAQ: 1,151.99
Position size: 70%, Stop loss adj: 120%
Favor: 반도체/IT / Avoid: 금융
```

## 커밋 이력
1. (이번 커밋) `feat: VIX + USD/KRW 매크로 수집 추가 — Yahoo Finance + BOK ECOS`

## 현재 상태 (Current State)
- 배포 완료 (job-worker, macro-council 이미지 rebuild + recreate)
- 546 tests passed, ruff clean
- Redis snapshot에 VIX/USD/KRW 데이터 존재 확인
- Council이 글로벌 매크로 컨텍스트를 정상적으로 활용

## 2026-02-23 전체 세션 통합 기록

오늘 총 6개 세션이 진행됨 (이 세션 포함):

### 1. Race Condition Fix (`f47dfed`, `b54ab29`)
- Redis 기동 경쟁 조건 해소 (BusyLoadingError/ConnectionError 30초 retry)
- Monitor /start, /stop 엔드포인트 추가
- Loki 볼륨 권한 수정 (root → uid 1000)
- Scanner watchlist reload 시 Gateway 재구독 트리거

### 2. WebSocket PINGPONG Fix (`7fc3c52`)
- KIS WebSocket PINGPONG echo 응답 추가 — ~10초 끊김 해결
- 비표준 _ping_loop 제거, 재연결 로직 while loop 전환
- Approval key 재연결 시 갱신

### 3. Telegram Fix (`605b032`, `5254991`)
- Telegram 커맨드 폴링 자동 시작 (lifespan에서)
- Redis BusyLoadingError 재시도 추가
- /watchlist 데이터소스 DB → Redis 전환

### 4. Cloudflare Tunnel Fix (session-2026-02-23-cloudflare-tunnel-fix.md)
- Tunnel 전체 재생성 (Jenkins 잔여 제거)
- Access Application + Email OTP 정책 재설정
- 새 tunnel token `.env` 반영

### 5. System Page Tabs (`dcbe1b5`, `5effd2e`, `ba7b047`)
- Dashboard System 페이지에 Workflows(Airflow) + Logs(Loki) 탭 추가
- Airflow 3 REST API v2 + JWT 인증 마이그레이션
- price_monitor start/stop DAG 제거 (상시 동작 전환)

### 6. VIX + USD/KRW 매크로 수집 (이번 세션)
- 위 내용 참조

## 다음 할 일 (Next Steps)
- [x] TODO.md #1: ROE 정기 갱신 Job (월간, 네이버 금융 크롤링) ✅
- [x] TODO.md #2: 분기 재무제표 정기 갱신 Job ✅
- [x] TODO.md #6: 명시적 장 오픈 시간 체크 추가 ✅
- [x] TODO.md #7: /watch, /unwatch 커맨드 실효성 확보 ✅
- [x] TODO.md #8: watchlist_histories DB 기록 프로세스 추가 ✅
- [ ] 매크로 수집 추가 지표 고려: DXY, Fed Rate, Treasury 10Y, BOK Rate 등

## Context for Next Session
다음 세션 시작 시 아래 파일들을 먼저 읽어주세요:
- `.ai/TODO.md` — 미해결 과제 목록
- `prime_jennie/services/jobs/app.py:665-730` — VIX/USD/KRW 수집 헬퍼 함수
- `prime_jennie/services/council/app.py:122-168` — GlobalSnapshot 로드 (VIX/USD/KRW 필드 매핑)

## 핵심 결정사항 (Key Decisions)
- **Yahoo Finance VIX 직접 조회**: 레거시 Finnhub VXX ETF 프록시 대신 실제 `^VIX` 사용 (가격 스케일 정확)
- **BOK ECOS API**: 레거시와 동일 소스, API 키를 `SecretsConfig`(prefix 없음)로 이전
- **에러 시 graceful 처리**: VIX/USD/KRW 조회 실패해도 기존 KOSPI/KOSDAQ 수집은 정상 진행 (각각 독립 try/except)
- **새 의존성 없음**: httpx는 이미 프로젝트에 포함

## 주의사항 (Warnings)
- Yahoo Finance API는 비공식 → User-Agent 헤더 필수, 향후 변경 가능성 있음
- BOK ECOS API 키 만료/제한 정책 확인 필요 (현재 레거시 키 재사용)
- `frontend/tsconfig.tsbuildinfo`, `scripts/run_backtest.py`, `uv.lock`은 여전히 untracked
