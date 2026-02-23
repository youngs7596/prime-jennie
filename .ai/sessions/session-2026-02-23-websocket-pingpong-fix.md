# Session Handoff: KIS WebSocket PINGPONG 응답 누락 수정

## 작업 날짜
2026-02-23 (월요일 밤, 장 마감 후)

## 작업 브랜치
`development`

## 완료된 작업

### 1. KIS WebSocket PINGPONG echo 추가
- **문제**: KIS 서버가 보내는 PINGPONG 메시지를 `_handle_message`에서 무시 → 서버가 무응답 판단 → ~10초 만에 연결 종료 + 재연결 반복
- **수정**: `_handle_message(ws, message)` — JSON 메시지 중 `tr_id == "PINGPONG"` 감지 시 동일 메시지를 `ws.send()`로 echo
- **파일**: `prime_jennie/services/gateway/streamer.py`

### 2. 비표준 `_ping_loop` 제거
- **문제**: `{"say": "hello"}`는 KIS 표준이 아닌 커스텀 keepalive → 효과 없음
- **수정**: `_ping_loop` 메서드 + `_ping_thread` + `PING_INTERVAL` 상수 제거
- KIS PINGPONG echo가 keepalive 역할 대체

### 3. 재연결 로직: 재귀 → while 루프
- **문제**: `on_close` → `self._ws_loop(approval_key)` 재귀 호출 → 스택 무한 누적
- **수정**: `_ws_loop` 내부를 `while self._is_running:` 루프로 변경, `on_close`는 로깅만 수행

### 4. 재연결 시 approval_key 갱신
- **문제**: 재연결할 때 만료된 approval_key 재사용 → 인증 실패 가능
- **수정**: `start()`에서 `self._base_url` 저장, 재연결 루프에서 `get_approval_key()` 재호출 (캐시 무효화 후)

## 발견된 이슈: 장 오픈 체크 부재

코드 전체 검색 결과, **buy-scanner / price-monitor / buy-executor / sell-executor** 어디에도 "장이 열려있는지" 체크하는 로직이 없음:

| 컴포넌트 | 장 오픈 체크 | 비고 |
|----------|-------------|------|
| Buy Scanner risk_gates | 09:00-09:15 no-trade + 14:00-15:00 danger zone만 있음 | **09:00 이전, 15:30 이후** 체크 없음 |
| Price Monitor | 없음 | tick 오면 무조건 평가 |
| Buy/Sell Executor | 없음 | signal 오면 무조건 주문 |
| Gateway `/api/market/is-market-open` | 있음 (시간 기반) | 다른 서비스에서 호출하지 않음 |

**현재 안전장치**: KIS WebSocket이 장외에 틱을 안 보내서 사실상 동작 안 함 (암묵적 의존)

→ TODO에 명시적 장 오픈 체크 추가 과제 등록

## 커밋
- `7fc3c52` — `fix: KIS WebSocket PINGPONG echo 응답 추가 — 연결 끊김 해결`

## 배포 상태
- development push → GitHub Actions Deploy 성공 (2m34s)
- 배포 후 4분+ WebSocket 연결 유지 확인 (이전: ~10초 만에 끊김)
- 장외 시간이라 PINGPONG/틱 수신은 내일 장중 확인 필요

## 서비스 기동 상태 (배포 직후 확인)
- **Gateway**: 기동 OK, WebSocket connected, 25종목 구독
- **Buy Scanner**: 기동 OK, watchlist 20종목 로드, tick consumer 대기
- **Price Monitor**: 기동 OK, 5포지션 로드, tick consumer 대기

## 다음 세션 TODO
- [ ] 재부팅 후 전체 서비스 정상 기동 확인 (`docker compose --profile infra --profile real ps`)
- [ ] Gateway WebSocket 연결 유지 확인 (이전 ~10초 끊김 → 수정 후 유지되는지)
- [ ] 장중 PINGPONG echo 로그 확인: `docker logs prime-jennie-kis-gateway-1 | grep -i pingpong`
- [ ] buy-scanner / price-monitor tick 수신 확인 (tick count 증가 로그)
- [ ] 장중 매수/매도 정상 발생 여부
- [ ] (중기) 명시적 장 오픈 시간 체크 gate 추가 검토
