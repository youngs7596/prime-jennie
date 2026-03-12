# TODO — 미해결 과제

> **정본**: 이 파일이 To-Do의 Single Source of Truth.
> 세션 파일의 "Next Steps"는 발견 시점 기록용이며, 추적은 이 파일에서만 한다.
> 완료된 항목은 `DONE.md`로 이동한다.

---

## 체크리스트 (확인 후 DONE 이동)

- [x] **#11** ~~WSJ 자동 파이프라인 동작 확인~~ — 코드 검증 완료 → DONE
- [x] **#12** ~~MarketCalendar gating 확인~~ — 백그라운드 스레드 자동화, 코드 검증 완료 → DONE
- [x] **#14** ~~trading_flags:stop 해제 판단~~ — stop 중 시그널 축적 없음, 해제 시 안전 확인 → DONE
- [x] **#15** ~~macro_quick 5분 Naver API rate limit~~ — 5분당 4건(0.8 req/min), 안전 확인 → DONE

---

## 1회성 분석 (조사 후 DONE 이동)

### ~~16. score=50 레거시 데이터 처리~~ → DONE
- ✅ 버그 기간(02-20~03-02) 33,709건 → EXAONE 재분석 완료 (03-10)
- 실제 원인: 구시스템은 reason 미저장 + model=None 버그 기간만 진짜 문제
- 재분석 후 평균 63.0점, 정상 분포 복원
- _발견: 03-05_

### 17. 폭락장(03-03) 사후분석
- trade_log + positions DB로 실제 피해 규모 확인
- 체결 확인 강화(Fix 1) 이전 매도 건 중 실체결 확인, 수동 보정 필요 여부
- _발견: 03-03_

---

## 개발 과제

### 3. 방산 대형주 스코어링 개선
- **현상**: 한화에어로(Q=62), 현대로템(Q=58), HD현대중공업(Q=57) — watchlist 미진입
- **원인**: PBR 8-11, PER 25-82 → Quality+Value 합산 낮음
- **해결안 후보**:
  - (A) 섹터별 PBR/PER 상대평가 (같은 섹터 내 백분위)
  - (B) 테마 모멘텀 가산점 (뉴스 감성 + 섹터 모멘텀 연동)
  - (C) 시가총액 상위 N개 자동 포함 (universe guarantee)
- **주의**: 한번에 너무 많이 바꾸지 않기
- _발견: 02-25_

### 4. E2E Mock KIS Gateway 테스트 구축
- 계획: `.claude/plans/memoized-splashing-toucan.md`
- Mock Gateway + BuyExecutor/SellExecutor E2E
- fakeredis + SQLite in-memory, 총 19개 테스트
- _발견: 02-25_

### 9. 전략 파라미터 퀀트 적합성 튜닝
- DIP_BUY: 범위 타이트 → 확장 검토
- MOMENTUM: 7% cap 과적합 → 국면별 차등
- GOLDEN_CROSS / MOMENTUM_CONT: 중간 우선순위
- **방법**: signal_logs 기반 한 항목씩. 동시 변경 금지
- _발견: 02-25_

### 18. WebSocket ↔ Polling 자동 전환
- 현재: `KIS_STREAMER_MODE` env var 수동 토글
- 개선: 연결 실패 시 자동 fallback, 또는 WebSocket 안정성 재테스트 후 고정
- _발견: 03-06_

---

## 개선 (여유 시)

### ~~5. Quant Scorer Shadow Comparison 정리~~ → DONE
- ✅ _log_shadow_comparison 100줄 삭제 (03-11)
- _발견: 02-25_

### 24. GAP_UP_REBOUND 전략 검증
- ✅ v1 구현 완료 (03-11): 전일대비 +2% 갭업 + 거래량 1.5x + 시가 유지
- ✅ 역사적 백테스트 분석 + 제니/민지 리뷰 완료 (03-12)
- ✅ 조건 강화 배포 (03-12): 전일 -3% 필수 + 갭업 15% 상한 + prev_day_return API
- 데이터 수집 중 (stop=1 상태에서 signal_logs 축적)
- [ ] 30~45 거래일 실전 시그널 데이터 확인 후 성과 평가
- [ ] 성과 양호 시 stop 해제 후 실거래 적용
- [ ] 이후 MEAN_REVERSION 전략 개발 착수
- _발견: 03-11_

### ~~19. 텔레그램 WSJ 요약 프롬프트 튜닝~~ → DONE
- ✅ 현재 품질 충분, 추가 튜닝 불필요 (03-11)
- _발견: 03-07_

### ~~20. VKOSPI 데이터 소스 확보~~ → DONE
- ✅ 무료 API 없음 확인, US VIX(Yahoo Finance) 유지 결정 (03-11)
- _발견: 03-06_

### 21. dev 환경 서비스 로컬 실행 테스트
- `.env.dev`로 scanner 등 개별 서비스 기동 확인
- _발견: 03-04_

### ~~22. GHCR deploy CI 타이밍 레이스~~ → DONE
- ✅ `branch=development` 필터 추가하여 해당 브랜치 CI만 확인 (03-09)
- _발견: 03-09_

### ~~23. daily_briefing_report execution_timeout 조정~~ → DONE
- ✅ timeout 5분→10분, retries 2→1 (멱등성 보호 있어 재시도 축소) (03-10)
- _발견: 03-09_
