# Session Handoff: Telegram 커맨드 미응답 수정

## 작업 날짜
2026-02-23 (월요일, 장 마감 후)

## 작업 브랜치
`development`

## 완료된 작업

### 1. Telegram 커맨드 폴링 자동 시작
- **문제**: Telegram 서비스 기동 시 커맨드 폴링이 자동 시작되지 않음 → `POST /start` 수동 호출 필요 → 재배포마다 커맨드 무응답
- **원인**: lifespan에서 체결 알림 consumer만 자동 시작, 커맨드 폴링은 `/start` 엔드포인트 호출에 의존
- **수정**: lifespan에서 커맨드 폴링도 자동 시작 (`_poll_loop` 스레드)
- **추가**: Redis `BusyLoadingError` 재시도 루프 (`_wait_for_redis`, 최대 30초)
- **파일**: `prime_jennie/services/telegram/app.py`
- **커밋**: `605b032`

### 2. /watchlist 커맨드 데이터 소스 변경 (DB → Redis)
- **문제**: `/watchlist` 응답에 `#None 신세계(0점, none)` 표시
- **원인**: DB `watchlist_histories`에는 레거시 데이터(2/19)만 있고 rank/hybrid_score/trade_tier 전부 NULL. Scout가 Redis `watchlist:active`에만 기록하고 DB에는 안 씀
- **수정**: handler에서 DB(`WatchlistRepository.get_latest`) 대신 Redis(`TypedCache[HotWatchlist]`)에서 읽도록 변경
- **파일**: `prime_jennie/services/telegram/handler.py`
- **커밋**: `5254991`

### 3. 전체 커맨드 점검 (24개)
- 22개 정상 동작 확인
- **`/watch`, `/unwatch` 무효**: `watchlist:manual` Redis hash에 기록하지만 Scanner가 읽지 않음 → TODO 등록

## 발견된 이슈

### watchlist_histories DB 미갱신
- Scout가 Redis에만 워치리스트 기록, DB에는 안 씀
- 최신 DB 데이터: 2026-02-19 (레거시 마이그레이션)
- 백테스트/분석용으로 DB 기록 프로세스 필요 → TODO #8

### /watch, /unwatch 무효
- `watchlist:manual` 키를 읽는 서비스 없음 → TODO #7

## 현재 상태 (Current State)
- Telegram 서비스 정상 동작: 폴링 자동 시작, `/watchlist` 정상 응답
- 서비스 상태 점검은 별도 창에서 진행 중

## 다음 할 일 (Next Steps)
- [x] (긴급) 별도 창 서비스 상태 점검 결과 확인 ✅
- [x] (TODO #7) /watch, /unwatch 실효성 확보 (Scanner 병합 또는 커맨드 제거) ✅
- [x] (TODO #8) watchlist_histories DB 기록 프로세스 추가 ✅

## Context for Next Session
다음 세션 시작 시 아래 파일들을 먼저 읽어주세요:
- `.ai/TODO.md` — 전체 미해결 과제
- `prime_jennie/services/telegram/app.py` — 폴링 자동 시작 로직
- `prime_jennie/services/telegram/handler.py` — 커맨드 핸들러 (watchlist Redis 전환)

## 핵심 결정사항 (Key Decisions)
- Telegram 폴링은 장 시간과 무관하게 항상 자동 시작 (Monitor/Scanner의 `/start`-`/stop` 패턴과 다름)
- `/watchlist`는 DB가 아닌 Redis에서 읽음 (DB는 분석용 스냅샷, Redis가 실시간 데이터)

## 주의사항 (Warnings)
- 다른 창에서 `streams.py`, `scanner/app.py` 수정 중일 수 있음 — 충돌 주의
- `watchlist_histories` DB는 현재 레거시 데이터만 있음 (분석 시 주의)
