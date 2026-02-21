# Prime Jennie 섹터 주도주 미포착 원인 진단

> 작성일: 2026-02-21
> 작성: Claude Code (진단 + 코드 분석)

## Context

KOSPI가 2025년 75% 급등, 2026년 2월 5,800선까지 도달. 상승을 주도한 섹터(반도체+115%, 조선/방산+97%, 금융+107%)의 핵심 종목을 Prime Jennie Scout가 거의 포착하지 못함.
진단 결과 **Scout Quant Score v2의 Value-biased 점수 체계**가 성장주 강세장에서 구조적으로 주도주를 배제하고 있음이 확인됨.

---

## Phase 1: Scout 유니버스 & 필터 체인 — "어디서 막혔나"

### 유니버스 (문제 없음)
- `prime_jennie/services/scout/universe.py`: 시총 상위 200개, is_active=True
- 12개 주도주 **전부 stock_masters에 존재**, is_active=True → 유니버스에는 포함됨

### Quant Score v2 구조 (핵심 병목)
**파일**: `prime_jennie/services/scout/quant.py`

| 팩터 | 배점 | 성장주 영향 |
|------|------|-----------|
| Momentum | 0-20 | RSI>70 → 5pt 중 **1pt만**. 52주고점 근처에서는 pullback 보너스도 없음 |
| Quality | 0-20 | ROE>15 → 10pt. 적자기업 → 0pt |
| **Value** | **0-20** | **PER>30 → 0.5pt(10pt만점), PBR>3 → 0pt(5pt만점), 52주고점근처 → 1.5pt(5pt만점)** |
| Technical | 0-10 | MA alignment → 정렬된 상승추세에서는 OK (5pt) |
| News | 0-10 | 뉴스 감성 기반, 중립적 |
| Supply/Demand | 0-20 | 외인·기관 수급 기반, 주도주에 유리할 수 있음 |

### 주도주 Quant Score 추정

**삼성전자** (유일한 1회 등장, 2026-01-11):
- quant_score = **52.37** → LLM 보정 후 hybrid = 58.22 → NOT tradable
- Value 추정: PER ~25배 → 2pt, PBR ~1.5 → 1pt, 52주 고점 근처 → 1.5pt = **~4.5pt/20pt**

**SK하이닉스** (0회 등장):
- PER 50배+ → 0.5pt, PBR 3배+ → 0pt, 52주 고점 → 1.5pt = **~2pt/20pt**
- 추정 quant_score: 35~45 (LLM 진출 기준 25 이상이지만 watchlist top20에 못 듦)

**조선/방산/원전** (전부 0회):
- PER 50~100배+ 또는 적자 → Value **0~2pt/20pt**
- RSI 상시 70+ → Momentum RSI 항목 **1pt/5pt**
- 추정 quant_score: **25~40** (대부분 LLM 진출 못하거나, 진출해도 low hybrid)

### DB 검증 결과

**watchlist_histories (prime-jennie, 최근 60일)**:

| 주도주 | 등장 횟수 | 점수 범위 | is_tradable |
|--------|---------|----------|------------|
| 삼성전자 | 1회(1/11) | 58.2 | 0 (NOT) |
| SK하이닉스 | **0회** | - | - |
| HD현대중공업 | **0회** | - | - |
| 한화에어로스페이스 | **0회** | - | - |
| 두산에너빌리티 | **0회** | - | - |
| 한화오션 | **0회** | - | - |
| KB금융 | 14회 | 60.9~79.1 | 1 (OK) |
| 신한지주 | 12회 | 64.1~80.6 | 1 (OK) |
| 현대차 | 14회 | 67.2~82.0 | 1 (OK) |
| 기아 | 13회 | 57.3~84.0 | 1 (OK) |
| 삼성바이오 | **0회** | - | - |
| 현대로템 | **0회** | - | - |

**레거시 watchlist 현재 스냅샷**: 신한지주(68.2)·KB금융(64.8)만 존재. 나머지 10개 주도주 **부재**.

**daily_quant_scores 테이블**: 최근 30일 데이터 **0건** (상세 점수 분해 DB 저장 안 됨)

### 통과 종목 vs 탈락 종목 비교

| 구분 | 대표 | PER | PBR | Value 추정 | Quant 추정 | 결과 |
|------|------|-----|-----|-----------|-----------|------|
| 통과 | KB금융 | 11.2 | 1.01 | 14pt+ | 65~78 | TIER1/RECON |
| 통과 | 현대차 | ~8 | ~0.6 | 16pt+ | 61~78 | TIER1/RECON |
| **탈락** | **SK하이닉스** | **50+** | **3+** | **2~3pt** | **35~45** | **미등장** |
| **탈락** | **HD현대중공업** | **70+** | **3+** | **1~2pt** | **30~40** | **미등장** |
| **탈락** | **삼성바이오** | **100+** | **3+** | **1~2pt** | **25~35** | **미등장** |

---

## Phase 2: Buy Scanner — "Scout를 통과해도 막힘"

### 7개 전략 요약

| 전략 | 상승추세 포착? | 제한 사항 |
|------|-------------|----------|
| GOLDEN_CROSS | O (교차시점만) | MA5가 MA20을 아래→위 돌파하는 **순간**만 |
| MOMENTUM | 제한적 | **7% 상한 캡** (chase prevention) |
| MOMENTUM_CONTINUATION | 제한적 | 2~5% 범위, **09:15~10:30만** |
| CONVICTION_ENTRY | 제한적 | **3% 상한**, 09:15~10:30, RSI<65 |
| VOLUME_BREAKOUT | O | 3x 거래량 + 신고가 돌파 |
| DIP_BUY | X (눌림만) | 고점 대비 하락 시에만 |
| RSI_REBOUND | X | **BULL에서 비활성** |

### Risk Gate 핵심 차단

- **Gate 4**: RSI > 75 → 모든 전략 신호 차단 (주도주 대부분 RSI 70~80)
- **Gate 9**: Scout BLOCKED tier → Scanner에서도 차단
- Scanner는 **watchlist 종목만 WebSocket 구독** → Scout 미통과 = Scanner 모니터링 대상 자체에서 제외

### 실제 매매 전략 분포 (최근 2개월)

주요 진입: MOMENTUM(가장 다수), GOLDEN_CROSS, MOMENTUM_CONTINUATION_BULL, RSI_OVERSOLD
주요 매매 종목: 효성, SK, 삼성생명, 영원무역, 포스코인터, 한국전력, CJ, 롯데쇼핑 등
→ **중소형 가치주·금융주 중심**, 반도체/조선/방산/원전 주도 섹터 거의 부재

---

## Phase 3: Regime 감지

- **완전 LLM 기반** (Council 서비스 3단계 파이프라인)
- 입력: VIX, KOSPI/KOSDAQ, 외인·기관 수급, 브리핑
- STRONG_BULL → 현금바닥 5%, 포지션승수 130%까지, MOMENTUM_CONTINUATION 활성
- **하지만 Scout가 주도주를 통과시키지 않으면 Regime 혜택이 의미 없음**

| 파라미터 | STRONG_BULL | BULL | SIDEWAYS | BEAR |
|---------|-----------|------|---------|------|
| 현금 바닥 | 5% | 10% | 15% | 25% |
| Bull 전략 활성 | O | O | X | X |
| Trailing Drop | 3.0% | 3.0% | 3.5% | 3.5% |

---

## Phase 4: 섹터 분석 기능

| 기능 | 상태 |
|------|------|
| 네이버 섹터 분류 (79→14그룹) | ✅ 구현 |
| 섹터 예산 HOT/WARM/COOL | ✅ 구현 |
| **섹터 모멘텀 Quant 팩터** | ❌ **없음** |
| **섹터 상대강도 분석** | ❌ **없음** |
| Council 섹터 입력 | ❌ 항상 빈 문자열 |
| Enrichment sector_momentum | ❌ 설계문서에만 존재 |

**핵심**: Quant Score v2는 100% 개별 종목 레벨. "이 섹터가 시장 주도 섹터인가?" 질문을 전혀 하지 않음.

---

## Phase 5: 실제 매수 이력 vs 주도주

| 섹터 | 주도주 | 시스템 매수? | 비고 |
|------|--------|-----------|------|
| 반도체 | 삼성전자 | 수동 1회(2/5) | Profit Floor 매도로 손실 |
| 반도체 | SK하이닉스 | ❌ 0건 | watchlist 0회 |
| 조선 | HD현대중공업 | ❌ 0건 | watchlist 0회 |
| 방산 | 한화에어로 | ❌ 0건 | watchlist 0회 |
| 원전 | 두산에너빌리티 | ❌ 0건 | watchlist 0회 |
| 조선 | 한화오션 | ❌ 0건 | watchlist 0회 |
| 금융 | KB금융 | ✅ 매수·매도 | 저PER→Value↑ |
| 금융 | 신한지주 | ✅ 매수·매도 | 저PER→Value↑ |
| 자동차 | 현대차 | ✅ 여러차례 | PER 적정→Value OK |
| 자동차 | 기아 | ✅ 여러차례 | PER 적정→Value OK |
| 바이오 | 삼성바이오 | ❌ 0건 | watchlist 0회 |
| 방산 | 현대로템 | ❌ 0건 | watchlist 0회 |

---

## 진단 종합: 병목 요약

```
유니버스(200개) → 전부 포함 ✅
  ↓
Quant Score v2 → ★ 여기서 탈락 ★
  - Value 팩터가 고PER(>30) 종목에 극심한 페널티 (0.5~2pt / 20pt)
  - RSI>70 → Momentum에서 1pt만 (5pt만점)
  - 52주 고점 근처 → Value에서 1.5pt만 (5pt만점)
  - 결과: 성장주 quant 35~50, 가치주 quant 60~78
  ↓
LLM 분석 → ±15pt clamp (quant 기준)
  - quant 40이면 LLM 최대 55 → 여전히 TIER2 이하
  ↓
Watchlist 선정 → top 20 by hybrid_score
  - 가치주(60~80)에 밀려 성장주(35~55) 진입 불가
  ↓
Scanner → watchlist에 없으면 모니터링 안 함
  - RSI guard(>75) 추가 차단
```

---

## 코드 레벨 상세 분석

### Quant Score v2 — 서브팩터별 코드 분석

#### `_value_score()` (quant.py:180-225) — 성장주 차단의 핵심

```python
# PER 할인 (0-10): PER>30이면 0.5pt밖에 안 줌
if ft.per < 8:     score += 10.0
elif ft.per < 12:  score += 7.0
elif ft.per < 18:  score += 4.0
elif ft.per < 30:  score += 2.0
else:              score += 0.5  # ← SK하이닉스(PER 50+) 여기

# PBR 평가 (0-5): PBR>3이면 0pt
if ft.pbr < 0.7:   score += 5.0
elif ft.pbr < 1.0:  score += 4.0
elif ft.pbr < 1.5:  score += 2.5
elif ft.pbr < 3.0:  score += 1.0
# PBR >= 3.0 → 0pt  ← SK하이닉스(PBR 3+) 여기

# 52주 고점 대비 (0-5): 고점 근처 = 1.5pt만 (추세 강도를 벌점으로 취급)
if drawdown < -30:   score += 2.0
elif drawdown < -15: score += 5.0
elif drawdown < -5:  score += 3.5
else:                score += 1.5  # ← 52주 고점 근처 = 강한 추세인데 벌점
```

**SK하이닉스 Value 추정**: 0.5 + 0 + 1.5 = **2pt/20pt**
**KB금융 Value 추정**: 7.0 + 4.0 + 3.5 = **14.5pt/20pt** (차이: 12.5pt)

#### `_momentum_score()` (quant.py:91-133) — RSI 과매수 페널티

```python
# RSI 기반 (0-5): 40-60이 "최적"이고 RSI>70은 1pt만
if 40 <= rsi <= 60:     score += 5.0
elif 30 <= rsi < 40 or 60 < rsi <= 70:  score += 3.5
elif rsi < 30:          score += 4.0  # 과매도 = 반등 잠재력
else:                   score += 1.0  # RSI>70 = 과매수 ← 주도주 대부분 여기
```

**문제**: 강한 모멘텀(RSI 70-80)을 "과매수"로 처벌. 주도주는 RSI 70-80 상시 유지.

### Analyst Risk Tag (analyst.py:92-135)

```python
# DISTRIBUTION_RISK: drawdown>-3% AND RSI>70 AND 외인<-1B AND 기관<-1B
# → 고점 근처 + 약간의 수급 악화만으로도 VETO (거래 차단)

# CAUTION: RSI>70이면 무조건 CAUTION
if rsi and rsi > 70:
    return RiskTag.CAUTION  # ← 주도주 RSI 70-80이면 전부 CAUTION
```

**문제**: BULL 국면에서도 RSI>70이면 CAUTION 태그. 모멘텀 구간을 위험으로 분류.

### Scanner Risk Gates (risk_gates.py:88-94)

```python
# Gate 4: RSI > 75 → 무조건 차단 (국면 무관)
def check_rsi_guard(rsi, max_rsi=75.0):
    if rsi > max_rsi:
        return GateResult(False, "rsi_guard", f"RSI {rsi:.1f} > {max_rsi}")
```

**문제**: BULL/STRONG_BULL에서도 RSI 75 이상이면 진입 차단.

---

## 개선 포인트 (검토용)

### 1. [최우선] 섹터 모멘텀 팩터 추가
- Quant Score에 7번째 팩터 `sector_momentum(0-10pt)` 추가
- HOT 섹터 종목 +5~10pt 부스트 → watchlist 진입 가능성 증가
- 섹터 20일 평균 수익률 기반 linear mapping
- 파일: `quant.py`, `enrichment.py`, `app.py`, `scoring.py`

### 2. [높음] Value Score 성장주 페널티 완화
- PER>30 → 0.5pt 대신 1.5~2pt (여전히 저PER보다는 낮지만 극단 페널티 해소)
- PBR>3 → 0pt 대신 1pt
- 52주 고점 근처 → 1.5pt 대신 3pt (강한 추세 인정)
- 파일: `quant.py` (`_value_score()`)

### 3. [높음] RSI 과매수 페널티 축소
- RSI 40-70: 5pt (현행 40-60)
- RSI 70-80: 3pt (현행 1pt)
- RSI >80: 1pt (극단만 페널티)
- 파일: `quant.py` (`_momentum_score()`)

### 4. [중간] DISTRIBUTION_RISK 거부권 완화
- 외인·기관 순매도 임계값 -1B → -3B (경미한 수급 악화로 VETO 안 걸리도록)
- BULL에서 CAUTION RSI 기준 70→80 (모멘텀 구간 보호)
- 파일: `analyst.py` (`classify_risk_tag()`)

### 5. [중간] Scanner RSI Guard 상향
- BULL: RSI guard 75 → 85
- SIDEWAYS: 75 유지
- 파일: `config.py`, `risk_gates.py`

### 6. [참고] daily_quant_scores DB 저장 복원
- 최근 30일 데이터 0건 → 저장 로직 확인 필요
- 진단 시 정확한 점수 분해 불가 원인

---

## 예상 효과 시뮬레이션

### SK하이닉스 (PER 50+, PBR 3+, RSI ~75, 반도체 HOT)

| 팩터 | Before | After | 변화 |
|------|--------|-------|------|
| Momentum | ~8pt | ~10pt | RSI 75→3pt (+2) |
| Quality | ~8pt | ~8pt | 변화없음 |
| Value | ~2pt | ~6pt | PER +1.5, PBR +1, 52주 +1.5 |
| Technical | ~5pt | ~5pt | 변화없음 |
| News | ~5pt | ~5pt | 변화없음 |
| Supply/Demand | ~10pt | ~10pt | 변화없음 |
| **Sector Momentum** | **없음** | **~8pt** | **HOT 섹터 부스트** |
| **Total** | **~38pt** | **~52pt** | **+14pt** |
| LLM +15 clamp | ~53pt | ~67pt | **TIER1 진입 가능** |

### HD현대중공업 (PER 70+, PBR 3+, RSI ~78, 조선 HOT)

| 팩터 | Before | After | 변화 |
|------|--------|-------|------|
| Total | ~32pt | ~48pt | +16pt |
| LLM +15 | ~47pt | ~63pt | **TIER1 경계** |

### KB금융 (PER 11, PBR 1.0, 금융 HOT) — 기존 통과 종목

| 팩터 | Before | After | 변화 |
|------|--------|-------|------|
| Total | ~68pt | ~76pt | +8pt (섹터 모멘텀만) |

→ 기존 가치주도 소폭 상승하지만, 성장주 개선폭(+14~16pt)이 훨씬 커서 **상대적 격차 대폭 축소**

---

## 근본적 질문 (토론 주제)

1. **Value 중심 vs Growth 적응형**: 현재 Quant Score가 Value 투자 성향. 강세장에서 Growth 주도주를 잡으려면 점수 체계 자체를 "시장 국면 적응형"으로 바꿔야 하나?
2. **섹터 로테이션 전략**: Scout가 개별 종목만 보는 것이 아니라, 먼저 "어떤 섹터가 주도하는가"를 판단하고 그 섹터 내에서 종목을 선정하는 top-down 방식이 필요한가?
3. **KODEX 200 하이브리드**: 강세장에서는 ETF 비중을 높이고 개별종목은 약세장/횡보장에서 알파를 추구하는 하이브리드 전략?
4. **LLM clamp 범위**: ±15pt가 너무 보수적? quant 40인 성장주를 LLM이 80으로 올리고 싶어도 55가 한계. 범위를 ±25로 확대?
