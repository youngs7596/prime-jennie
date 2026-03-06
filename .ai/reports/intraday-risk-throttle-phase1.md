# Intraday Risk Throttle — Phase 1 데이터 수집 가능성 보고서

**작성일**: 2026-03-06
**대상**: Phase 1 (P0) — 데이터 수집 가능성 확인

---

## 1. KOSPI/KOSDAQ 지수 실시간 구독 현황

| 항목 | 현황 | 모듈명 |
|------|------|--------|
| KOSPI 지수 실시간 구독 | **X** | - |
| KOSDAQ 지수 실시간 구독 | **X** | - |
| KOSPI 일중 등락률 수집 | **O** (15분 폴링) | `jobs/app.py` → `macro_collect_global()` |
| KOSDAQ 일중 등락률 수집 | **O** (15분 폴링) | 동일 |

### 상세

- **KIS WebSocket** (`streamer.py`): `H0STCNT0` TR만 구독 — watchlist 종목 체결가만 수신. 지수 코드(0001/1001) 구독 없음
- **KIS REST Poller** (`poller.py`): 동일하게 종목 현재가(`FHKST01010100`)만 폴링
- **Naver mobile API**: `fetch_index_data("KOSPI")` → `fluctuationsRatio` 필드로 일중 등락률 제공
  - 현재: Airflow `macro_quick` DAG에서 15분 간격 호출
  - **확장 가능**: 동일 API를 30초 간격으로 폴링 가능 (rate limit 여유)
- **추가 구독 필요 TR 코드**: 없음 (Naver API로 충분)

### 데이터 소스 (확정)

```
KOSPI 등락률: Naver mobile API → m.stock.naver.com/api/index/KOSPI/basic
  → fluctuationsRatio: -1.48 (실시간, delayTime=0)
  → 현재 macro_collect_global()에서 수집 → Redis macro:data:snapshot:{date}
```

---

## 2. VKOSPI 데이터 수집 가능 여부

| 소스 | 가용성 | 비고 |
|------|--------|------|
| KIS REST API | **미확인** | VKOSPI TR 코드 미문서화. FHKUP02100100(업종현재가)에서 파생 지수 조회 가능성 있으나 테스트 불가 (장중 API 키 필요) |
| Naver mobile API | **X** | `index/VKOSPI/basic` → HTTP 409 (StockConflict) |
| Naver desktop 페이지 | **X** | `sise_index.naver?code=VKOSPI` → KOSPI 페이지로 폴백 (유효 코드 아님) |
| Yahoo Finance | **X** | `^KS200VIX`, `^VKOSPI` 등 → 404 Not Found |
| KRX Open API | **X** | 403 Forbidden (API 키 미보유) |
| investing.com | **△** | `kospi-200-volatility` 페이지 존재하나 API 403, 스크래핑 시 불안정 (장외 0.00 반환) |

### 결론

VKOSPI 실시간/준실시간 데이터는 **무료 API로 안정적 수집 불가**.

### 대안: US VIX 활용 (기존 인프라)

- **이미 수집 중**: `_fetch_vix()` (Yahoo Finance, `jobs/app.py:856`)
- **기존 circuit breaker에서 활용**: VIX ≥ 35 → BEAR 하향 (`jobs/app.py:1079`)
- **갱신 주기**: `macro_quick` DAG 15분 간격 → Redis `macro:data:snapshot:{date}` 저장
- **한계**: US 장중 시간대(한국 22:30~05:00)에만 실시간, 한국 장중에는 전일 종가
- **실용성**: 글로벌 공포 지표로서 충분. 한국 장중 VIX 급등은 전일 미국 장에서 발생하므로 당일 아침 데이터로 반영 가능

**권장**: VKOSPI 수집 생략, US VIX를 그대로 활용

---

## 3. 외국인 선물 순매수 데이터

| 소스 | 가용성 | 비고 |
|------|--------|------|
| KIS REST API | **X** | 선물 투자자별 수급 TR 없음 |
| KIS WebSocket | **X** | 선물 관련 실시간 TR 미구독 |
| Naver 투자자 수급 | **△** | `fetch_investor_flows()` — 현물 시장 일별 집계만 (당일 장중 미제공) |
| Naver 종목별 외국인 | **△** | `fetch_stock_frgn_data()` — 종목별 외인 순매수 (현물, 일별) |
| KRX Open API | **X** | API 키 미보유 |

### 결론

외국인 **선물** 순매수 실시간 데이터는 **수집 불가**.
외국인 **현물** 순매수 일별 집계는 가능하나 장중 실시간 데이터 아님.

**권장**: Phase 1에서 외국인 선물 데이터 제외. KOSPI 등락률 + VIX 2개 지표로 시작

---

## 4. 기존 인프라 활용 가능 요소

### 4-1. Circuit Breaker (이미 운영 중)

```
위치: jobs/app.py _check_circuit_breaker() (L1091-1154)
트리거:
  - KOSPI ≤ -2% → BEAR (position_multiplier ≤ 0.6)
  - KOSPI ≤ -4% → STRONG_BEAR (position_multiplier ≤ 0.6)
  - VIX ≥ 35 → BEAR (position_multiplier ≤ 0.6)
주기: macro_quick DAG 15분 간격
동작: downgrade only, 기존 context 필드 보존
```

### 4-2. Position Multiplier 전달 경로 (검증 완료)

```
Council/CircuitBreaker → TradingContext (Redis macro:trading_context, TTL 24h)
  → Scanner (5분마다 reload) → BuySignal.context
  → BuyExecutor → position_sizing.py (L193: final_size *= multiplier)
```

### 4-3. Redis 데이터 키

```
macro:data:snapshot:{date}  — GlobalSnapshot JSON (KOSPI 등락률, VIX 등)
macro:trading_context       — TradingContext JSON (regime, position_multiplier 등)
```

---

## 5. Phase 2 구현 권장 사항

### 사용 가능 데이터 (2개)

| 지표 | 소스 | 갱신 주기 | 현재 저장 위치 |
|------|------|-----------|---------------|
| KOSPI 일중 등락률 | Naver mobile API | 15분 (확장 가능: 30초) | `macro:data:snapshot:{date}` |
| US VIX | Yahoo Finance | 15분 | `macro:data:snapshot:{date}` |

### 사용 불가 데이터 (2개)

| 지표 | 사유 | 대안 |
|------|------|------|
| VKOSPI | 무료 API 없음 | US VIX로 대체 |
| 외국인 선물 순매수 | 실시간 소스 없음 | 생략 (KOSPI 하락률이 이미 반영) |

### 권장 구조

1. **기존 circuit breaker 확장** (새 daemon 불필요)
   - `macro_quick` (15분) 이미 KOSPI + VIX 수집 + circuit breaker 실행
   - Intraday Risk Throttle 로직을 `_check_circuit_breaker()` 내에 통합
   - 별도 daemon보다 단순하고 기존 데이터 파이프라인 재활용

2. **5단계 리스크 레벨 (KOSPI 등락률 + VIX 기반)**
   - NORMAL: KOSPI > -1%, VIX < 25 → multiplier 1.0
   - CAUTION: KOSPI ≤ -1% or VIX ≥ 25 → multiplier 0.8
   - WARNING: KOSPI ≤ -2% or VIX ≥ 30 → multiplier 0.6
   - DANGER: KOSPI ≤ -3% or VIX ≥ 35 → multiplier 0.3
   - CRITICAL: KOSPI ≤ -4% → multiplier 0.0 (매수 중단)

3. **갱신 주기 개선 옵션**
   - 현재 15분이 부족하면 `macro_quick` Airflow interval 5분으로 단축
   - 또는 별도 lightweight poller 스레드를 job-worker에 추가 (30초 간격, Naver API만)

---

## 6. 요약

| 항목 | 결과 |
|------|------|
| KOSPI 지수 구독 | **X** (WebSocket 미구독), Naver API 폴링으로 대체 가능 |
| KOSDAQ 지수 구독 | **X** (WebSocket 미구독), Naver API 폴링으로 대체 가능 |
| VKOSPI 수집 | **X** (무료 API 없음), US VIX로 대체 |
| 외국인 선물 순매수 | **X** (실시간 소스 없음), 생략 권장 |
| 추가 구독 필요 TR 코드 | **없음** (Naver API + Yahoo Finance로 충분) |
| 기존 인프라 재활용 | **O** (circuit breaker + macro_quick + position_multiplier 경로) |
