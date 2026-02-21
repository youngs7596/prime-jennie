# 섹터 주도주 포착 개선 — 개발 보고서

> 일시: 2026-02-21
> 브랜치: `development`
> 변경: 8 files changed, +180 -25 lines (2차 피드백 반영 포함)

---

## 문제 진단

Scout Quant Score v2가 **Value-biased 점수 체계**여서 고PER 성장주를 구조적으로 배제.

| 종목 | PER | PBR | 기존 Value 점수 | Watchlist 등장 |
|------|-----|-----|----------------|---------------|
| KB금융 | 11 | 1.0 | ~14.5pt/20 | 14회 |
| 현대차 | ~8 | ~0.6 | ~16pt/20 | 14회 |
| **SK하이닉스** | **50+** | **3+** | **~2pt/20** | **0회** |
| **HD현대중공업** | **70+** | **3+** | **~1.5pt/20** | **0회** |
| **한화에어로스페이스** | **50+** | **3+** | **~2pt/20** | **0회** |

병목 체인: `Value 극저 → Quant 35~45 → LLM ±15 clamp → 최대 60 → Watchlist top20 미진입 → Scanner 모니터링 제외`

---

## 구현 내용 (7개 개선 포인트)

### 1. 섹터 모멘텀 팩터 추가 (7번째 팩터, 0-10pt)

**파일**: `quant.py`, `scoring.py`, `enrichment.py`, `app.py`

HOT 섹터 종목에게 최대 +10pt 부스트. 기존 6팩터(100pt) → 7팩터(110pt raw, 100pt cap).

```
섹터 20일 평균 수익률 → 선형 매핑
  -5% 이하 → 0pt (COOL 섹터)
  +5%     → 5pt (중립)
  +15%    → 10pt (HOT 섹터)
```

**파이프라인 순서**: Phase 2(Enrichment) → **Phase 2.5(섹터 모멘텀 계산)** → Phase 3(Quant)

**최소 종목 수 필터**: 섹터 내 종목 5개 미만이면 대표성 부족으로 제외 → 중립 5pt 적용.

### 2. Value Score 성장주 페널티 완화

**파일**: `quant.py` `_value_score()`

| 구간 | Before | After | 변경 |
|------|--------|-------|------|
| PER 30-50 | 2.0pt | 2.5pt (30미만), 2.0pt (30-50) | 세분화 |
| PER ≥50 | 0.5pt | 1.5pt | **+1.0pt** |
| PBR 1.5-3.0 | 1.0pt | 1.5pt | +0.5pt |
| PBR ≥3.0 | 0pt (implicit) | 1.0pt | **+1.0pt** |
| 52주 고점 근접 (>-5%) | 1.5pt | 3.0pt | **+1.5pt** |

SK하이닉스 Value 추정: 2pt → 5.5~6pt (+3.5pt)

### 3. RSI 과매수 페널티 Regime 연동

**파일**: `quant.py` `_momentum_score()`, `score_candidate()`

| RSI 범위 | Before | SIDEWAYS/BEAR | BULL/STRONG_BULL |
|---------|--------|--------------|-----------------|
| 40-70 | 3.5~5pt | 5pt | 5pt |
| **70-80** | **1pt** | **3pt** | **5pt (페널티 없음)** |
| >80 | 1pt | 1pt | 1pt |

`score_candidate()`에 `market_regime` 파라미터 추가. `app.py`에서 `context.market_regime` 전달.

**핵심**: BULL에서 RSI 75는 "과매수"가 아니라 "강한 추세". 페널티 제거.

### 4. DISTRIBUTION_RISK 거부권 완화

**파일**: `analyst.py` `classify_risk_tag()`

| 조건 | Before | After |
|------|--------|-------|
| DISTRIBUTION_RISK 외인 순매도 | < -1B | < **-3B** (3배 강화) |
| DISTRIBUTION_RISK 기관 순매도 | < -1B | < **-3B** |
| CAUTION RSI 임계값 | > 70 | > **80** |

### 5. Scanner RSI Guard 국면별 상향

**파일**: `config.py`, `risk_gates.py`

| 국면 | RSI Guard |
|------|-----------|
| SIDEWAYS/BEAR | 75 (기존 유지) |
| **BULL/STRONG_BULL** | **85** (신규) |

### 6. 섹터 최소 종목 수 필터 [피드백 반영]

**파일**: `app.py` Phase 2.5

종목 5개 미만인 섹터는 20일 수익률 평균의 대표성이 부족. `len(r) >= 5` 필터 추가.
해당 섹터 종목은 `sector_avg_return_20d = None` → 중립 5pt 적용.

### 7. Shadow Mode 로깅 [피드백 반영]

**파일**: `quant.py` `_log_shadow_comparison()`

변경 전(v2.0) 기준으로 점수를 재계산하고, 차이가 ±3pt 이상이면 INFO 로그 출력.

```
[SHADOW] SK하이닉스(000660): v2.0=38.2 → v2.1=53.7 (Δ+15.5) [RSI +2.0, Value +3.5, Sector +10.0]
```

비교 항목:
- **RSI delta**: 변경 전(40-60=5, 60-70=3.5, >70=1) vs 현행
- **Value delta**: 변경 전(PER≥30=0.5, PBR≥3=0, 고점=1.5) vs 현행
- **Sector delta**: 변경 전 없음 vs 현행 (전량 delta)

**용도**: 배포 후 1~2주간 watchlist 스냅샷 비교 시 "어떤 종목이 v2.0에서는 탈락했을 것" 정량적 판단 가능.

---

## 변경 파일 상세

| # | 파일 | 내용 |
|---|------|------|
| 1 | `domain/scoring.py` | `sector_momentum_score` 필드, validator 100pt cap |
| 2 | `services/scout/enrichment.py` | `sector_avg_return_20d` 필드 |
| 3 | `services/scout/quant.py` | 7번째 팩터, Value/RSI 완화, Regime 연동, shadow 로깅, neutral 동적 합산 |
| 4 | `services/scout/app.py` | Phase 2.5 섹터 모멘텀 (최소 5종목 필터), context 로드 Phase 3 전으로 이동 |
| 5 | `services/scout/analyst.py` | DISTRIBUTION_RISK/CAUTION 임계값 완화 |
| 6 | `domain/config.py` | `rsi_guard_bull_max: 85.0` |
| 7 | `services/scanner/risk_gates.py` | BULL 국면별 RSI guard |
| 8 | `tests/unit/services/test_scout_quant.py` | 6개 신규 테스트, 기존 3개 수정 |

---

## 예상 효과 (SK하이닉스 시뮬레이션, BULL 국면)

| 팩터 | Before | After | Delta |
|------|--------|-------|-------|
| Momentum (RSI 75) | ~6pt | **~10pt** | **+4** (BULL: RSI 70-80 = 5pt) |
| Quality (ROE 15+) | ~15pt | ~15pt | 0 |
| Value (PER 50+, PBR 3+) | ~2pt | ~5.5pt | +3.5 |
| Technical | ~5pt | ~5pt | 0 |
| News | ~5pt | ~5pt | 0 |
| Supply/Demand | ~10pt | ~10pt | 0 |
| **Sector Momentum (반도체 +15%)** | **없음** | **~10pt** | **+10** |
| **Total (100pt cap)** | **~43** | **~60.5** | **+17.5** |
| **LLM +15pt clamp 적용** | **~58** | **~75.5** | -- |
| **Trade Tier** | **BLOCKED** | **TIER1** | -- |

---

## 검증 결과

```
✅ pytest tests/unit/services/test_scout_quant.py -v  →  30 passed
✅ ruff check prime_jennie/ tests/                    →  All checks passed
✅ pytest tests/ -x                                   →  492 passed, 0 failed
```

---

## 테스트 추가/수정

**신규 (6건)**:
- `TestSectorMomentumScore::test_hot_sector_high_score` — HOT 섹터 15% → 9.5pt+
- `TestSectorMomentumScore::test_cool_sector_low_score` — COOL 섹터 -5% → 0.5pt-
- `TestSectorMomentumScore::test_none_returns_neutral` — 데이터 없음 → 5.0pt
- `TestSectorMomentumScore::test_moderate_sector` — 중간 5% → 4-6pt
- `TestMomentumScore::test_rsi_70_80_bull_no_penalty` — BULL에서 RSI 70-80 페널티 없음
- `TestScoreCandidate::test_bull_regime_boosts_momentum` — BULL vs SIDEWAYS 점수 비교

**수정 (3건)**:
- `test_total_equals_sum_of_subscores` — sector_momentum_score 포함
- `test_insufficient_data_returns_neutral` — total 50→`sum(V2_NEUTRAL.values())`=55
- `test_high_per_low_score` — assertion `< 5.0` → `< 6.0` (하한 완화 반영)

---

## 아키텍처 변경 없음

- DB 스키마 변경 없음 (Alembic migration 불필요)
- Redis 키 변경 없음
- API 인터페이스 변경 없음 (score_candidate 내부 파라미터만 추가, 외부 API 영향 없음)
- 설정값은 환경변수 `SCANNER_RSI_GUARD_BULL_MAX`로 오버라이드 가능 (기본 85.0)
- 롤백: 코드 revert만으로 완전 복원 가능

---

## 피드백 반영 이력

| 피드백 | 반영 |
|--------|------|
| RSI 페널티 Regime 연동 (BULL에서 70-80 = 5pt) | ✅ `_momentum_score(is_bull)` |
| 섹터 모멘텀 최소 종목 수 필터 | ✅ `app.py` `len(r) >= 5` |
| Shadow mode 로깅 | ✅ `_log_shadow_comparison()` |
| DISTRIBUTION_RISK도 Regime 연동 | ❌ 일괄 -3B로 상향 (BEAR에서도 -3B는 합리적 수준으로 수용) |
