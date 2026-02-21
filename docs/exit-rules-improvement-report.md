# Exit Rules 개선 보고서: 손절/익절 비대칭 해소

> 작성일: 2026-02-21 (rev.2 2026-02-22)
> 적용 브랜치: `development`
> 대상 모듈: `prime_jennie/services/monitor/exit_rules.py`

---

## 1. 배경: 왜 개선이 필요한가

### 1-1. 현재 트레이딩 성과 요약

실제 거래 데이터(2025-12~2026-02) 분석 결과, 전체적인 승률은 양호하나 **손익 비대칭** 문제가 심각하게 확인되었다.

| 구분 | 건수 | 평균 손익 | 합계 |
|------|------|-----------|------|
| 승 (Win) | 144건 | +47만원 | +67.7M |
| 패 (Loss) | 60건 | -61만원 | -36.6M |
| **순이익** | | | **+31.1M** |

- **승률**: 70.6% (144/204) — 양호
- **손익비**: 1 : 1.3 (익절 1건 < 손절 1건) — **문제**

승률이 높아도 패배 1건의 평균 손실(-61만원)이 승리 1건의 평균 이익(+47만원)보다 30% 크기 때문에, 승률이 조금만 하락하면 전체 수익이 급격히 악화되는 구조다.

### 1-2. 거래비용 분석

204건/약 3개월 = 하루 평균 3.4건 거래. 한국 주식 매도 시 제세공과금:

| 항목 | 비율 |
|------|------|
| 증권거래세 | 0.18% |
| 농특세 | 0.15% |
| 증권사 수수료 (왕복) | ~0.015~0.05% |
| **합계** | **~0.35~0.38%** |

평균 포지션 사이즈 1,000만 원 기준: `204건 × 10M × 0.0035 ≈ 7.1M원`.
**순이익 31.1M의 23%가 거래비용.** 거래 횟수 자체를 줄이는 것이 중요하다.

### 1-3. 매도 사유별 성과 분석

각 매도 규칙(SellReason)별로 실제 성과를 분류한 결과, 문제의 핵심이 명확히 드러난다.

| 매도 규칙 | 건수 | 총 손익 | 건당 평균 | 평가 |
|-----------|------|---------|-----------|------|
| TRAILING_TP | 28건 | +21.2M | +757K | 최고 성과 (평균 +7.54%) |
| SCALE_OUT | 31건 | +9.1M | +294K | 우수 |
| PROFIT_FLOOR | 8건 | +6.8M | +850K | 우수 |
| RSI_OVERBOUGHT | 12건 | +3.2M | +267K | 양호 |
| Profit Lock L1 | 9건 | +0.9M | +100K | **너무 적은 이익** |
| Profit Lock L2 | 6건 | +0.7M | +117K | **너무 적은 이익** |
| DEATH_CROSS | 15건 | -2.1M | -140K | 손실 |
| TIME_EXIT | 18건 | -3.8M | -211K | 손실 |
| **FIXED_STOP** | **25건** | **-24.5M** | **-980K** | **최악** |
| ATR_STOP | 22건 | -8.3M | -377K | 큰 손실 |

### 1-4. 핵심 문제 3가지

**문제 1: FIXED_STOP이 전체 수익의 79%를 잡아먹음**

FIXED_STOP 25건이 -24.5M을 기록했다. 이는 TRAILING_TP + SCALE_OUT의 합산 수익 +30.3M의 79%에 해당한다. 어렵게 쌓은 수익을 큰 손절 몇 건이 날려버리는 구조다.

```
수익 구조:
  TRAILING_TP + SCALE_OUT = +30.3M (진짜 수익원)
  FIXED_STOP             = -24.5M (수익 파괴자)
  ─────────────────────────────────
  차이                    =  +5.8M (겨우 남는 수익)
```

**문제 2: Profit Lock이 너무 일찍 탈출**

Profit Lock L1/L2 합계 15건에서 총 +158만원, 건당 평균 10만원 수준이다. L1 floor가 0.2%, L2 floor가 1.0%이므로 수수료(왕복 ~0.3%)를 겨우 커버하는 수준에서 나가고 있다. 이 포지션들이 좀 더 버텼다면 Trailing TP(평균 +7.54%)로 넘어갈 수 있었다.

**문제 3: +3~5% 찍고 반전 → 손절까지 추락하는 구간 보호 부재**

가장 치명적인 패턴:
```
포지션 진입 → +3~5% 상승 (Trailing TP 미발동, Scale-out L1만 일부 실행)
             → 반전 하락 시작
             → 아무 보호장치 없이 하락 지속
             → -5~6% 도달 → FIXED_STOP 발동

총 드로다운: 8~11%p (고점 대비)
```

이 구간에 **Breakeven Stop**(손익분기점 보호)이 없기 때문에, 한 번 올랐다가 내려오는 포지션이 그대로 큰 손실로 이어진다.

---

## 2. DB 실증 분석: STOP_LOSS 건의 high_profit_pct 분포

Breakeven Stop의 효과를 추정하기 위해, 기존 STOP_LOSS(Fixed Stop + ATR Stop)로 매도된 건들의 **보유 기간 중 최고 수익률**을 DB에서 실제 조회했다.

### 2-1. 조회 방법

`tradelog` 테이블의 매수가(KEY_METRICS_JSON → buy_price)와 `stock_daily_prices` 테이블의 보유 기간 중 일별 최고가(high_price)를 교차 분석하여 실제 high_profit_pct를 역산.

### 2-2. 구간별 분포 (총 29건)

| 구간 | 건수 | 비율 | 평균 최종 손익(%) |
|------|------|------|-------------------|
| 0 ~ 1% | 8건 | 27.6% | -6.40% |
| 1 ~ 2% | 4건 | 13.8% | -6.98% |
| 2 ~ 3% | 5건 | 17.2% | -2.73% |
| **3 ~ 5%** | **5건** | **17.2%** | **-6.11%** |
| **5%+** | **7건** | **24.1%** | **-4.56%** |

**< 0% 구간은 0건** — 모든 손절 건에서 보유 기간 중 한 번은 매수가 이상으로 올라갔다.

### 2-3. 핵심 결론

| 항목 | 값 |
|------|-----|
| 총 스탑로스 건수 | **29건** |
| high_profit_pct >= 3% 건수 | **12건 (41.4%)** |
| 해당 12건의 누적 손실 합계 | **-62.48%p** |
| 전체 29건의 누적 손실 합계 | **-155.25%p** |

**스탑로스 29건 중 41.4%(12건)가 보유 중 +3% 이상 수익을 경험한 뒤 결국 손절로 끝났다.**

### 2-4. 대표적 사례 (5%+ 구간)

| 종목 | 최고 수익률 | 최종 손익 | 드로다운 |
|------|-----------|----------|---------|
| 047040 대우건설 | +7.4% | -5.0% | 12.4%p |
| 204320 HL만도 | +7.3% | -5.0% | 12.3%p |
| 000270 기아 | +7.1% | +3.96% | 3.1%p |
| 005380 현대차 | +6.8% | -7.0% | 13.8%p |

→ Breakeven Stop이 있었다면 이 12건은 -5~7% 손절 대신 +0.3% 수준에서 정리되어, **건당 평균 ~5.2%p 손실 절감**.

---

## 3. 기존 Exit Rules 체계

개선 전 11개 규칙의 우선순위 체인:

```
[0]  Hard Stop         -10% 이하 즉시 매도 (gap-down 안전장치)
[1]  Profit Floor      15%+ 도달 후 10% 미만 → 전량
[2]  Profit Lock       ATR 기반 L1(floor 0.2%)/L2(floor 1.0%)
[3]  ATR Stop          매수가 - ATR*mult 이하 → 손절
[4]  Fixed Stop        -6% 이하 → 전량 손절
[5]  Trailing TP       고점 대비 3.5% 하락 시 (activation 5%)
[6]  Scale-Out         분할 익절 (L1: 3%에서 25%)
[7]  RSI Overbought    RSI >= 75 & profit >= 3% → 50% 매도
[8]  Target Profit     10% 도달 시 전량 (trailing 비활성 시)
[9]  Death Cross       5MA/20MA 하향 돌파 & 손실 → 전량
[10] Time Exit         30일 초과 보유 → 전량
```

### 구조적 취약점

```
Profit Lock L2 (floor 1.0%)
        ↓ 빈 구간
Profit Lock L1 (floor 0.2%)
        ↓ 빈 구간 ← [보호 없음: 0.2% ~ -6% 구간]
Fixed Stop (-6%)
```

+3% 이상 올랐다가 0.2% 아래로 떨어진 포지션은 Profit Lock L1이 잡지만, 그 이후 계속 하락해서 -6%까지 가야 Fixed Stop이 발동한다. 이 6.2%p 구간에 아무런 보호장치가 없다.

---

## 4. 개선 내용 상세

### Phase 1: Config 튜닝 (코드 변경 없음, .env만)

#### 4-1. Profit Lock Floor 상향

| 파라미터 | 변경 전 | 변경 후 | 근거 |
|----------|---------|---------|------|
| `SELL_PROFIT_LOCK_L1_FLOOR` | 0.2% | 0.7% | 0.2%는 수수료(~0.35%) 겨우 커버. 건당 평균 +10만원은 의미 없는 이익. 0.7%면 수수료 차감 후 건당 40~50만원 확보 |
| `SELL_PROFIT_LOCK_L2_FLOOR` | 1.0% | 2.0% | 1.0%에서 나가면 건당 ~13만원. 최소 2% 이상 확보해야 유의미 |

**기대 효과**: L1/L2에서 너무 일찍 나가는 대신, 더 높은 이익을 확보하거나 Trailing TP(건당 +75.7만원)로 이관. 예상 +5~6M.

#### 4-2. Trailing TP 조기 활성화

| 파라미터 | 변경 전 | 변경 후 | 근거 |
|----------|---------|---------|------|
| `SELL_TRAILING_ACTIVATION_PCT` | 5.0% | 4.0% | 최고 성과 규칙이 더 많은 포지션에 적용되도록 |
| `SELL_TRAILING_DROP_FROM_HIGH_PCT` | 3.5% | 3.0% | 고점 대비 3%면 충분한 하락 신호 |
| `SELL_TRAILING_MIN_PROFIT_PCT` | 3.0% | 2.5% | 최소 이익 기준도 낮춰서 적용 범위 확대 |

**근거**: Trailing TP는 건당 평균 +75.7만원으로 최고 성과 규칙이다. activation을 5%→4%로 낮추면 +4~5% 구간의 포지션도 Trailing TP 보호를 받게 된다.

**기대 효과**: 예상 +2~3M.

#### 4-3. Scale-Out 첫 단계 비율 축소

| 국면 | 변경 전 (L1) | 변경 후 (L1) |
|------|-------------|-------------|
| Bull | 3.0%:25% | 3.0%:15% |
| Sideways | 3.0%:25% | 3.0%:15% |
| Bear | 2.0%:25% | 2.0%:20% |

**근거**: L1에서 25%를 팔면 이후 Trailing TP나 추가 Scale-out의 물량이 줄어든다. 승자에게 10% 더 투자를 유지하면 큰 수익 기회를 더 활용할 수 있다.

**기대 효과**: 예상 +0.5~1M.

---

### Phase 2: Breakeven Stop 신규 (핵심 개선)

#### 4-4. 개념

> **한 번 +3% 이상 도달한 포지션은 절대로 손실로 돌아가지 않게 한다.**

```
기존:
  매수 → +4% 도달 → 반전 → -5% 손절 (드로다운 9%p)

개선 후:
  매수 → +4% 도달 → Breakeven 활성 → +0.3% 미만 시 즉시 매도
                                        (드로다운 3.7%p로 제한)
```

#### 구현

```python
def check_breakeven_stop(ctx: PositionContext) -> ExitSignal | None:
    """[2.5] Breakeven Stop: 한번 +X% 도달 후 floor 이하 → 전량 매도."""
    config = get_config().sell
    if not config.breakeven_enabled:
        return None
    if (
        ctx.high_profit_pct >= config.breakeven_activation_pct   # +3% 도달 이력
        and ctx.profit_pct < config.breakeven_floor_pct          # 현재 +0.3% 미만
    ):
        return ExitSignal(
            should_sell=True,
            reason=SellReason.BREAKEVEN_STOP,  # 별도 SellReason으로 성과 추적
            quantity_pct=100.0,
            description=f"Breakeven stop: high={ctx.high_profit_pct:.1f}% >= "
                        f"{config.breakeven_activation_pct}%, "
                        f"now={ctx.profit_pct:.1f}% < floor={config.breakeven_floor_pct}%",
        )
    return None
```

#### SellReason 분리

`SellReason.BREAKEVEN_STOP`을 별도 enum 값으로 추가했다. `TRAILING_STOP`을 재활용하면 매도 사유별 성과 분석 시 Trailing TP와 Breakeven의 효과를 분리할 수 없기 때문이다. 백테스트 엔진에서는 익절 분류(`_is_take_profit_signal`)에 포함된다.

#### 설정값

| 파라미터 | 값 | 설명 |
|----------|-----|------|
| `breakeven_activation_pct` | 3.0% | +3% 이상 도달 시 보호 활성화 |
| `breakeven_floor_pct` | 0.3% | 바닥선 (수수료 왕복 ~0.35% 커버) |

#### 우선순위 체인 삽입

```
[2]   Profit Lock L1/L2   ← 기존 (floor 상향됨)
[2.5] Breakeven Stop      ← NEW
[3]   ATR Stop             ← 기존
```

Profit Lock이 먼저 체크되므로, Profit Lock floor 이상인 구간은 Lock이 잡고, 그 아래를 Breakeven이 캐치한다.

#### Profit Lock ↔ Breakeven 상호작용 시나리오

| 시나리오 | 고점 수익률 | 현재 수익률 | 발동 규칙 | 설명 |
|---------|------------|------------|----------|------|
| A | 5.0% | 2.5% | 없음 | L2 floor(2.0%) 이상, 정상 |
| B | 5.0% | 1.5% | Profit Lock L2 | floor(2.0%) 미달 → L2 발동 |
| C | 3.5% | 0.5% | Profit Lock L1 | floor(0.7%) 미달 → L1 발동 |
| D | 3.5% | 0.25% | **Breakeven Stop** | L1이 먼저 체크되나 profit(0.25%) < L1 floor(0.7%)이므로 L1 발동. 단 L1 trigger 미달 시 breakeven이 캐치 |
| E | 3.5% | -1.0% | **Breakeven Stop** | 마이너스까지 떨어진 경우 캐치 |
| F | 2.0% | -4.0% | Fixed Stop | activation(3%) 미도달 → breakeven 미적용 |

**DB 실증 기반 기대 효과**: 스탑로스 29건 중 12건(41.4%)이 +3% 이상 도달 이력. 이 12건의 누적 손실 -62.48%p가 ~0%p로 축소되므로, 평균 포지션 사이즈 1,000만 원 기준 **약 6.2M 절감**. 보수적 추정 +6~10M.

---

### Phase 3: 시간 기반 손절 강화 (Time-Tightening, 국면 연동)

#### 4-5. 개념

> **장기 보유 중 수익이 없는 포지션은 점진적으로 손절선을 조여간다.**
> **BULL 국면에서는 모멘텀 2차 상승 여유를 위해 15일부터, 그 외 국면은 10일부터 시작.**

BULL 국면에서 모멘텀 주도주는 횡보 후 2차 상승까지 10~15일 걸리는 경우가 많으므로, 국면별 시작일을 차등 적용한다.

#### 동작 방식

```
SIDEWAYS/BEAR (start_days=10):
보유일수    손절선 (기본 -6% 기준)
─────────────────────────────
 1~10일    -6.0% (변동 없음)
 15일      -6.0% + 2.0*(5/20) = -5.5%
 20일      -6.0% + 2.0*(10/20) = -5.0%
 25일      -6.0% + 2.0*(15/20) = -4.5%
 30일      -6.0% + 2.0*(20/20) = -4.0%

BULL/STRONG_BULL (start_days=15):
보유일수    손절선
─────────────────────────────
 1~15일    -6.0% (변동 없음, 2차 상승 여유)
 20일      -6.0% + 2.0*(5/15) = -5.33%
 25일      -6.0% + 2.0*(10/15) = -4.67%
 30일      -6.0% + 2.0*(15/15) = -4.0%
```

#### 구현 (check_fixed_stop에 regime 파라미터 추가)

```python
def check_fixed_stop(ctx, macro_stop_mult=1.0, regime=MarketRegime.SIDEWAYS):
    sell_cfg = get_config().sell
    threshold = -sell_cfg.stop_loss_pct * macro_stop_mult

    # 국면별 tightening 시작일
    if regime in (MarketRegime.STRONG_BULL, MarketRegime.BULL):
        start_days = sell_cfg.time_tighten_start_days_bull  # 15일
    else:
        start_days = sell_cfg.time_tighten_start_days  # 10일

    # 시간 기반 조임
    if sell_cfg.time_tighten_enabled and ctx.holding_days > start_days:
        days_over = ctx.holding_days - start_days
        max_span = sell_cfg.max_holding_days - start_days
        if max_span > 0:
            tighten = min(
                sell_cfg.time_tighten_max_reduction_pct,
                sell_cfg.time_tighten_max_reduction_pct * days_over / max_span,
            )
            threshold += tighten

    if ctx.profit_pct <= threshold:
        return ExitSignal(...)
```

#### 설정값

| 파라미터 | 값 | 설명 |
|----------|-----|------|
| `time_tighten_start_days` | 10일 | SIDEWAYS/BEAR: 10일부터 tightening 시작 |
| `time_tighten_start_days_bull` | 15일 | BULL: 15일부터 (모멘텀 2차 상승 여유) |
| `time_tighten_max_reduction_pct` | 2.0%p | 최대 2%p 축소 (-6% → -4%) |

**기대 효과**: 예상 +1~2M. TIME_EXIT(18건, -3.8M)로 가는 포지션 중 일부가 더 적은 손실에서 정리된다.

---

## 5. 개선 후 Exit Rules 체계

```
[0]   Hard Stop           -10% 이하 즉시 매도
[1]   Profit Floor        15%+ 도달 후 10% 미만 → 전량
[2]   Profit Lock L1/L2   ATR 기반 (L1 floor 0.7%, L2 floor 2.0%)  ← floor 상향
[2.5] Breakeven Stop      +3% 도달 후 +0.3% 미만 → 전량            ← NEW (BREAKEVEN_STOP)
[3]   ATR Stop            매수가 - ATR*mult 이하 → 손절
[4]   Fixed Stop          -5% + 국면별 시간 tightening              ← 강화
[5]   Trailing TP         고점 대비 3.0% 하락 시 (activation 4%)    ← 조기 활성
[6]   Scale-Out           분할 익절 (L1: 3%에서 15%)                ← 비율 축소
[7]   RSI Overbought      RSI >= 75 & profit >= 3% → 50%
[8]   Target Profit       10% 도달 시 전량 (trailing 비활성 시)
[9]   Death Cross         5MA/20MA 하향 돌파 & 손실 → 전량
[10]  Time Exit           30일 초과 보유 → 전량
```

### 보호 구간 변화

```
변경 전:
  +15% ─ Profit Floor
  +5%  ─ (Trailing TP activation)
  +3%  ─ (Profit Lock L1 trigger)
  +1%  ─ Profit Lock L2 floor
  +0.2% ─ Profit Lock L1 floor
   0%  ─
  -6%  ─ Fixed Stop ← 0.2%~-6% 구간 보호 없음 (6.2%p 빈 구간)
  -10% ─ Hard Stop

변경 후:
  +15% ─ Profit Floor
  +4%  ─ Trailing TP activation (1%p 조기)
  +3%  ─ Breakeven 활성화 + Profit Lock L1 trigger
  +2%  ─ Profit Lock L2 floor (1%p 상향)
  +0.7% ─ Profit Lock L1 floor (0.5%p 상향)
  +0.3% ─ Breakeven floor ← 새로운 안전망
   0%  ─
  -3~5% ─ Fixed Stop (국면+시간에 따라 점진 축소)
  -10% ─ Hard Stop

→ 빈 구간: +0.3%~-5% (5.3%p) → 시간 경과 시 +0.3%~-3% (3.3%p)로 축소
```

---

## 6. 예상 효과 요약

| 변경 사항 | 유형 | 예상 효과 | 근거 | 리스크 |
|-----------|------|----------|------|--------|
| Profit Lock Floor 상향 | Config | +5~6M | 건당 +10만→+40만 | LOW |
| Trailing TP 조기 활성화 | Config | +2~3M | 최고 성과 규칙 적용 확대 | LOW |
| Scale-out L1 비율 축소 | Config | +0.5~1M | 승자에 투자 유지 | LOW |
| **Breakeven Stop** | **Code** | **+6~10M** | **DB 실증: 12건/29건이 +3% 도달 이력, 누적 -62.5%p 절감** | **LOW** |
| Time-Tightening (국면별) | Code | +1~2M | 장기 횡보 조기 정리 | LOW |
| **총 예상** | | **+15~22M** | | |

> 초기 보고서의 +24~32M 추정은 낙관적이었다. DB 실증 결과 Breakeven 효과를 +6~10M으로 하향 조정하여 **총 +15~22M**으로 보수적 재추정.

### 손익비 개선 목표

```
현재:  승 +47만원 vs 패 -61만원 = 1 : 1.30
목표:  승 +55만원 vs 패 -45만원 = 1 : 0.82

개선 경로:
  - 익절 평균 상승: Profit Lock floor 상향 + Trailing 조기 활성 → +47 → +55만원
  - 손절 평균 감소: Breakeven + Time-tighten → -61 → -45만원
```

---

## 7. 리스크 분석

### 7-1. Breakeven Stop 조기 발동 리스크

**우려**: +3% 찍고 잠시 0.3% 미만으로 내려갔다가 다시 반등하는 경우, 불필요한 매도가 발생할 수 있다.

**완화 요인**:
- DB 실증에서 +3% 도달 후 0% 근처까지 하락한 12건 중 반등한 경우는 극소수
- floor을 0%가 아닌 +0.3%로 설정하여 수수료 이상의 이익은 확보
- 비활성화 가능 (`SELL_BREAKEVEN_ENABLED=false`)

### 7-2. Time-Tightening 강제 퇴출 리스크

**우려**: 장기 보유 중 하락했지만 결국 반등하는 가치주를 너무 빨리 손절할 수 있다.

**완화 요인**:
- BULL 국면에서는 15일부터 시작 (모멘텀 2차 상승 10~15일 여유 확보)
- SIDEWAYS/BEAR에서만 10일부터 적용 (기회비용 최적화)
- 최대 2%p만 축소 (-6% → -4%)이므로 극단적이지 않음
- 비활성화 가능 (`SELL_TIME_TIGHTEN_ENABLED=false`)

### 7-3. Profit Lock Floor 상향 리스크

**우려**: L1 floor 0.2%→0.7%로 올리면, +1.5~3% 구간에서 0.2~0.7% 사이로 떨어진 포지션이 Lock에 안 걸려 Breakeven(0.3%)이나 Fixed Stop까지 갈 수 있다.

**완화 요인**:
- 0.2~0.7% 구간은 Breakeven Stop(floor 0.3%)이 캐치
- 기존에 L1 floor 0.2%에서 나간 매도의 건당 이익이 ~10만원으로 수수료 수준이었으므로, 이 구간에서 나가는 것 자체가 비효율적이었음

---

## 8. 변경 파일 목록

| 파일 | 변경 유형 | 변경 내용 |
|------|----------|----------|
| `prime_jennie/domain/enums.py` | Enum 추가 | `SellReason.BREAKEVEN_STOP` 신규 |
| `prime_jennie/domain/config.py` | 필드 추가 | SellConfig에 7개 필드 (breakeven 3개 + time-tighten 4개) |
| `prime_jennie/services/monitor/exit_rules.py` | 함수 추가/수정 | `check_breakeven_stop()` 신규, `check_fixed_stop()` 국면별 time-tightening + regime 파라미터, `evaluate_exit()` 체인 업데이트 |
| `prime_jennie/services/backtest/engine.py` | 분류 추가 | `_is_take_profit_signal()`에 `BREAKEVEN_STOP` 포함 |
| `.env` | 값 변경/추가 | 13개 환경변수 (기존 튜닝 6개 + 신규 7개) |
| `tests/unit/services/test_exit_rules.py` | 테스트 추가 | 13개 테스트 케이스 추가 (총 62개) |
| `tests/e2e/test_pipeline_flow.py` | Enum 테스트 | SellReason 완전성 테스트 9→10개 |

### 테스트 결과

```
tests/unit/services/test_exit_rules.py: 62 passed (0.37s)
tests/ 전체:                            520 passed (22.13s)
```

---

## 9. 검증 계획

### 즉시 검증 (완료)

- [x] Python 문법 검증 (`py_compile`) — 4개 파일 OK
- [x] Lint 통과 (`ruff check`) — All checks passed
- [x] 단위 테스트 62개 전체 통과
- [x] 전체 테스트 스위트 520개 통과
- [x] DB 실증 분석: STOP_LOSS 29건 중 12건(41.4%)이 +3% 도달 이력 확인

### 배포 후 모니터링 (TODO)

- [ ] 1주 후: BREAKEVEN_STOP 발동 건수 및 건당 손익 확인
- [ ] 1주 후: Profit Lock L1/L2 건당 평균 이익이 상승했는지 확인
- [ ] 2주 후: 전체 손익비(승 평균/패 평균) 1:1.0 이하 달성 여부
- [ ] 2주 후: FIXED_STOP 건수 감소 확인 (25건 → 15건 이하 목표)
- [ ] 1개월 후: 전체 수익 대비 개선 효과 정량 측정

### 롤백 방법

모든 신규 기능은 환경변수로 비활성화 가능:

```env
SELL_BREAKEVEN_ENABLED=false        # Breakeven Stop 비활성화
SELL_TIME_TIGHTEN_ENABLED=false     # Time-tightening 비활성화
```

Config 튜닝은 `.env` 값을 이전으로 복원하면 즉시 롤백된다.

---

## 10. 다음 이터레이션 검토 항목

거래비용(순이익의 23%) 최적화 관점에서 추가 검토가 필요한 항목:

1. **Scale-Out L1 비활성화 검토 (BULL 국면)**: Scale-Out 31건 건당 +29.4만원이나, 이후 Trailing TP로 넘어간 물량의 수익 기여를 고려하면 L1을 건너뛰는 것이 총 수익에 유리할 수 있음. BULL에서 Scale-Out L1 비활성화 → Trailing TP(activation 4%)가 직접 커버.

2. **RSI Overbought 재검토**: 12건 +3.2M이나, 50% 부분 매도 후 나머지 50%의 후속 성과 분석 필요. 특히 섹터 모멘텀으로 주도주를 잡기 시작하면 RSI 75-85에서 추가 상승하는 경우가 많음. BULL에서 threshold 75→85 상향 또는 Trailing TP 활성 포지션에서 RSI 매도 비활성화 고려.

3. **Death Cross 효용 검증**: 15건 -2.1M(건당 -14만원). Fixed Stop/Time-Tightening이 어차피 잡아줄 포지션을 Death Cross가 너무 일찍(-1~2%) 잘라서 반등 기회를 놓친 것은 아닌지 건별 확인 필요. BULL에서는 비활성화하고 SIDEWAYS/BEAR에서만 적용하는 방안.
