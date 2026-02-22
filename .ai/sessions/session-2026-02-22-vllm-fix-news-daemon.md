# Session Handoff: vLLM Fix + News Pipeline Daemon 디버깅

**일시**: 2026-02-22 13:30~14:10 KST
**브랜치**: development

---

## 완료된 작업

### 1. vLLM LLM unhealthy 해결 (이전 세션)
- **원인**: CUDA graph capture 후 uvicorn event loop deadlock (network_mode: host 환경)
- **수정**: `--enforce-eager` 플래그 추가 + `gpu-memory-utilization` 0.90→0.85
- VRAM 사용량: 23.3GB → 21.1GB (정상 — enforce-eager + gpu-mem 차이)

### 2. Alembic migration 003 적용
- stock_disclosures, stock_minute_prices 테이블 이미 create_all로 생성됨
- `alembic stamp 003`으로 version 동기화 완료

### 3. News Pipeline Daemon Loop 디버깅
- **문제**: cycle 1 완료(04:42) 후 9시간째 cycle 2 미진입 (daemon thread hang)
- OS 레벨 스레드 확인 — TID 7 alive(hrtimer_nanosleep), `_pipeline_status["loop_cycle"]` = 1
- **수정**: 디버그 로깅 강화 + BaseException 처리 추가
  - `[cycle N] Starting`, `[cycle N] Done. Sleeping Ns (market/off-hours)` 로그 추가
  - `except BaseException` 으로 silent crash 방지
  - daemon 종료 시 `daemon_running=False` 반영
- **재배포 후 결과**: cycle 1 → sleep 30분 → cycle 2 정상 진입 확인
- 이전 hang 원인은 아직 정확히 불명 (재현 안 됨)

---

## TODO (다음 세션)

### [P0] 뉴스 수집 0건 원인 조사
- cycle 1, 2 모두 `Collected 0 articles` — 토요일이라 0건일 수도 있지만 확인 필요
- 조사 포인트:
  1. **collector.run_once()** 로직 확인 — 네이버 뉴스 크롤링은 되지만 "published" 판정 기준이 뭔지 (날짜 필터? 중복 필터?)
  2. **DB 기존 데이터와 중복 체크** — article_url 기준 중복 제거가 너무 공격적인지
  3. **토요일 vs 평일 차이** — 월요일 장중에 재확인
  4. **Redis stream 확인** — `XLEN stream:news:raw`로 스트림에 메시지가 쌓이는지
  5. **2/19 이전 뉴스 처리가 정상이었는지** — my-prime-jennie에서 수집한 건지, prime-jennie 자체 수집인지 확인

### [P1] GitHub Actions Runner systemd 등록
- 재부팅 시 runner 자동 시작 안 됨
- `sudo ./svc.sh install youngs75` 필요 (sudo 권한 필요)

### [P2] LLM provider=ollama 확인
- LLM Factory가 FAST tier에 ollama를 선택하는데, ollama는 실행 중이 아님
- vLLM을 써야 하는 것 아닌지 확인 필요
- `prime_jennie/infra/llm/factory.py`의 tier 선택 로직 점검

---

## 현재 시스템 상태

| 항목 | 상태 |
|------|------|
| 전체 컨테이너 (24개) | healthy |
| vLLM LLM (EXAONE-32B-AWQ) | healthy, enforce-eager |
| vLLM Embed (kure-v1) | healthy |
| VRAM | 21.1GB / 24.5GB |
| News Pipeline | daemon 동작 중, cycle 2 진행 |
| News DB (2/20~2/22) | 0건 (조사 필요) |
| Alembic | 003 stamp 완료 |

## 변경 파일
- `prime_jennie/services/news/app.py` — 디버그 로깅 + BaseException 처리
