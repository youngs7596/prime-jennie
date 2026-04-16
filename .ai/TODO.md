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

### ~~17. 폭락장(03-03) 사후분석~~ → DONE
- ✅ DB 실증 분석 완료 (03-22)
- 당일 실현손실 -2,067,164원, 최대 낙폭 -32.0M(-15.3%)
- Fix 1 false negative 2건(대한항공/한국전력) MANUAL_SYNC로 해소 확인
- 4건 버그 모두 03-03 당일 수정 배포, 재발 방지 완료
- _발견: 03-03_

---

## 개발 과제

### ~~3. 방산 대형주 스코어링 개선~~ → DONE
- ✅ 섹터별 PBR/PER 백분위 상대평가 구현 (03-13)
- _발견: 02-25_

### ~~4. E2E Mock KIS Gateway 테스트 구축~~ → DONE
- ✅ 이미 구현 완료 확인 (03-22): MockTransport 12 endpoints, 47 테스트 전체 통과
- mock_gateway (httpx.MockTransport) + fakeredis + SQLite in-memory
- buy_flow 8건, sell_flow 8건, order_confirmation 7건, full_cycle 3건, pipeline_flow 21건
- _발견: 02-25_

### 9. 전략 파라미터 퀀트 적합성 튜닝 ⏸️ 보류
- DIP_BUY: 범위 타이트 → 확장 검토
- MOMENTUM: 7% cap 과적합 → 국면별 차등
- GOLDEN_CROSS / MOMENTUM_CONT: 중간 우선순위
- **방법**: signal_logs 기반 한 항목씩. 동시 변경 금지
- **보류 사유 (03-22)**: stop=1 상태에서 실거래 P&L 데이터 없음. signal_logs 10거래일분이나 BEAR/SIDEWAYS 국면이라 DIP_BUY 0건, GOLDEN_CROSS 0건, MOMENTUM_CONT 0건 — 통계 분석 불가. stop 해제 후 최소 30거래일 실전 데이터 축적 필요.
- _발견: 02-25_

### 18. WebSocket ↔ Polling 자동 전환 ⏸️ 보류
- 현재: `KIS_STREAMER_MODE` env var 수동 토글, Polling 모드 운영 중
- 개선: 연결 실패 시 자동 fallback, 또는 WebSocket 안정성 재테스트 후 고정
- **보류 사유 (03-22)**: Polling 3초 간격이 4종목+안정적 운영. stop=1 상태에서 실시간 틱 지연 무영향. stop 해제 시점에 WebSocket 재테스트 후 결정.
- _발견: 03-06_

---

### 27. Prime Jennie v3 Phase 1 착수
- **설계 문서 (4개, `/home/youngs75/projects/` 에 저장됨)**:
  - `prime_jennie_v3_phase0_design.md` — 전체 아키텍처, 3 repo 분리, Stage 0~3 권한 체계
  - `POSITION_SHEET_SPEC.md` — 포지션 시트 JSON 전수 명세 (exit rule 7종, edge case 8건)
  - `SCOUT_CODE_GENERATION.md` — Scout 코드 생성 + 샌드박스 격리 명세
  - `MACRO_GATE_SPEC.md` — 바이너리 게이트 + size_multiplier 명세
- **리뷰 피드백 (반영 필요)**:
  - POSITION_SHEET exit rule에 `profit_floor`, `death_cross` 2종 추가 필요
  - SCOUT consensus 데이터 접근 경로 명확화
  - MACRO_GATE 이산화 테이블 경계값(half-open interval) 명시
- **Phase 1 착수 시**: 4 Track 병렬 (A: 인프라, B: 느린 루프, C: 빠른 루프, D: Screening Executor)
- **선행 조건**: v2 재가동 검토 (Track A와 병행 가능)
- _발견: 04-16_

---

## 개선 (여유 시)

### ~~5. Quant Scorer Shadow Comparison 정리~~ → DONE
- ✅ _log_shadow_comparison 100줄 삭제 (03-11)
- _발견: 02-25_

### 25. 비전통적 데이터 상관관계 실험 재검증 (60일)
- 5분봉 데이터 60거래일 축적 시점 (약 2026-05-20) 재실행
- 대상: 실험 1(디커플링), 3(시간대별 수급), 4(섹터 리드-래그)
- 실험 3: 5분봉 수급 데이터 확보 여부 먼저 확인
- 실험 4: 시장 베타 차감(잔차 분석) 적용하여 재분석
- 상세: `experiments/SUMMARY.md`
- _발견: 04-05_

### 26. 비전통적 데이터 상관관계 실험 재검증 (120일)
- 5분봉 데이터 120거래일 축적 시점 (약 2026-08-중순) 재실행
- 대상: 실험 2(캔들 체형 클러스터링) — train/test 분리 out-of-sample 검증
- 실험 5(선물 OI): 데이터 확보 시 첫 실행
- 상세: `experiments/SUMMARY.md`
- _발견: 04-05_

### 24. GAP_UP_REBOUND 전략 검증
- ✅ v1 구현 완료 (03-11): 전일대비 +2% 갭업 + 거래량 1.5x + 시가 유지
- ✅ 역사적 백테스트 분석 + 제니/민지 리뷰 완료 (03-12)
- ✅ 조건 강화 배포 (03-12): 전일 -3% 필수 + 갭업 15% 상한 + prev_day_return API
- 데이터 수집 중 (stop=1 상태에서 signal_logs 축적)
- **경과 (03-22)**: signal_logs 0건. 03-09 KOSPI -5.96%→03-10 +5.35% 반등에도 개별 종목 레벨에서 4조건 동시 충족 사례 없음. 조건이 매우 엄격하여 발화 빈도 극저 — 조건 완화 검토 필요할 수 있음
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

### ~~21. dev 환경 서비스 로컬 실행 테스트~~ → DONE
- ✅ WSL2에서 .env.dev로 전체 서비스 로컬 기동 확인 (03-22)
- DB(jennie_db_dev)/Redis(DB 1) 원격 연결 OK, Scanner uvicorn 기동 OK
- Buyer/Seller/Monitor/Jobs 임포트 전부 성공
- _발견: 03-04_

### ~~22. GHCR deploy CI 타이밍 레이스~~ → DONE
- ✅ `branch=development` 필터 추가하여 해당 브랜치 CI만 확인 (03-09)
- _발견: 03-09_

### ~~23. daily_briefing_report execution_timeout 조정~~ → DONE
- ✅ timeout 5분→10분, retries 2→1 (멱등성 보호 있어 재시도 축소) (03-10)
- _발견: 03-09_
