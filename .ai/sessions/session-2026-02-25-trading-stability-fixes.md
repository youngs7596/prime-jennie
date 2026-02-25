# Session: 매매 안정성 긴급 수정 (2026-02-25)

## Summary
장 시작 후 WATCHLIST_CONVICTION 전략 폭주로 인한 중복매수 및 현금 고갈 문제 긴급 수정.
Telegram /sell 명령 미작동, Cash/Total Asset 표시 불일치 등 다수 버그 해결.

## Changes Made

### 1. WATCHLIST_CONVICTION 폭주 → 비활성화
- **원인**: conviction 전략이 risk gate(쿨다운 포함)를 우회하여 매 틱마다 시그널 발행
- **피해**: 한화 중복매수(51+7주), 영원무역·JB금융지주 불필요 매수 → 현금 500만원까지 고갈
- **수정**: `conviction_entry_enabled` 기본값 `False`, `.env`도 `false`로 변경
- **파일**: `config.py`, `.env`

### 2. Telegram /sell, /sellall 미작동 수정
- **원인**: Telegram handler가 raw dict로 `stream:sell-orders`에 XADD → consumer가 `payload` 키를 찾지 못해 "Empty payload"로 무시
- **수정**: `SellOrder` 모델 생성 → `model_dump_json()` → `{"payload": ...}` 형태로 발행
- 보유 수량 조회, 현재가 조회 로직도 추가
- **파일**: `telegram/handler.py`

### 3. Cash 표시 정확도 개선
- **변경 이력**: `dnca_tot_amt` → `nxdy_excc_amt` → `prvs_rcdl_excc_amt` → **`TTTC8908R` (매수가능금액 API)**
- `get_buying_power()` 메서드 추가: `nrcvb_buy_amt` (미수없는매수금액) 사용
- KIS 앱 "현금최대가능"과 정확히 일치 확인
- **파일**: `gateway/kis_api.py`

### 4. Total Asset 계산 방식 변경
- **이전**: `tot_evlu_amt` (KIS 내부 현금 기준, 매수가능금액과 불일치)
- **변경**: `cash_balance(매수가능금액) + scts_evlu_amt(주식평가)` 직접 계산
- **파일**: `gateway/kis_api.py`

### 5. sync-positions KIS 기준 전면 덮어쓰기
- **이전**: qty/price mismatch만 개별 업데이트 (elif 버그로 둘 다 다르면 price 무시)
- **변경**: 모든 공통 종목을 KIS 기준으로 덮어쓰기 (qty, avg, total_buy_amount)
- price 변경 시 `stop_loss_price` 자동 재계산
- `high_watermark` 갱신 (KIS current_price > 기존 watermark 시)
- NULL `sector_group` 자동 조회/설정
- INSERT 시 `sector_group`, `stop_loss_price` 자동 설정
- **파일**: `jobs/app.py`, `tests/unit/services/test_sync_positions.py`

### 6. KIS Gateway 토큰 캐시 볼륨
- 재시작 시 토큰 파일 소실 → KIS 토큰 발급 rate limit (403) 문제 방지
- `/docker_data/kis_token:/app/config` 볼륨 마운트 추가
- **파일**: `docker-compose.yml`

### 7. 규칙 추가
- 장중 kis-gateway 재시작 절대 금지 (토큰 403 리스크)
- **파일**: `.ai/RULES.md`, `MEMORY.md`

## Commits (unpushed, development branch)
```
55fa72d fix: total_asset를 cash(매수가능금액) + stock_eval로 직접 계산
61f32db fix: 매매 안정성 개선 (conviction 비활성화, 매도 직렬화, cash 정확도)
9c65dd6 fix: WATCHLIST_CONVICTION 시그널 쿨다운 적용 (폭주 방지)
104513d fix: _get_positions 실패 시 매수 차단 (빈 리스트 반환 → 중복매수 방지)
977695d fix: dashboard Cash를 익일정산금액(nxdy_excc_amt)으로 변경
e0f846f fix: 한화(000880) 섹터 매핑 조선/방산으로 변경 + 종목별 오버라이드 지원
382b5ce fix: buyer _persist_buy에서 high_watermark + stop_loss_price 초기값 설정
c5949e0 fix: sync-positions에서 stock_masters 미등록 종목 자동 생성 (ETF 등 FK 위반 방지)
```

## Deployed (장중 단일 서비스 배포)
- buy-scanner: conviction 비활성화 + 쿨다운 적용
- buy-executor: _get_positions null safety
- telegram: /sell SellOrder 직렬화
- kis-gateway: 매수가능금액 API + total_asset 계산 + 토큰 볼륨

## DB 수동 보정 (2026-02-25)
- 한화·영원무역·대한조선: 평균단가 KIS 기준 보정, stop_loss/watermark 재계산
- 한화·팬오션·JB금융지주: 사용자가 Telegram으로 매도 완료

## Unstaged Changes (다른 세션)
- `trading.py`, `deepseek_cloud.py`, `executor.py`, `position_sizing.py`, `scout/analyst.py`, `repositories.py`, `monitor/app.py`, `test_llm_providers.py`
- 이 세션에서 건드리지 않은 파일들 — 별도 확인 필요

## Context for Next Session
- `.ai/RULES.md` — 장중 배포 금지 규칙
- `prime_jennie/services/gateway/kis_api.py` — 매수가능금액 API, total_asset 계산
- `prime_jennie/services/jobs/app.py` — sync-positions 로직
- `prime_jennie/services/telegram/handler.py` — 수동 매매 명령

## Next Steps
- **15:30 이후 git push** — 8개 커밋 대기 중
- docker-compose.yml의 `SCANNER_CONVICTION_ENTRY_ENABLED: "true"` 환경변수 → `"false"`로 변경 필요 (현재 .env가 override하지만 정리 차원)
- Unstaged 파일들 확인 및 정리
- KIS 앱 갱신 후 total_asset 정확도 재검증
