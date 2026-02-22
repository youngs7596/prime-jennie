# Exit Rules v2 — 청산 단계 축소를 통한 수익률 극대화

> 선행 작업: `13b039b` feat: Exit Rules 손절/익절 비대칭 해소
> 브랜치: `development`
> 우선순위: 중 (급하진 않지만 수익률 직접 영향)

---

## 배경

Exit Rules v1 개선(Breakeven Stop, Time-Tightening, Config 튜닝) 배포 후, 부분 매도 단계가 수익 잠재력을 조기 차단하는 구조적 문제를 확인했다.

**거래비용 실측 (2026-02-22, 최근 3주 DB 실증):**
- 매수 52건(8.6억) + 매도 90건(11억)
- 뱅키스 수수료: 0.0140527% (매수/매도 동일)
- 매도 세금: 코스피 0.20% (거래세 0.05% + 농특세 0.15%) — 전량 코스피 거래
- **총 거래비용: 249만원** (수수료 28만 + 세금 221만)
- 순이익 1,149만원 대비 **21.6%**
- ~~기존 추정 0.38%/건은 3배 과대 (2025 이전 세율 + 영업점 수수료 기준)~~
- **실제 왕복 비용: ~0.228%**

**핵심 인사이트: 비용 절감보다 물량 유지 효과가 압도적**
- 19건 절감 시 비용 절감은 27만원에 불과
- 그러나 L1/RSI에서 조기 매도하지 않은 물량이 Trailing TP(+8~13%)까지 올라가는 것이 본질적 가치

---

## 검토 항목 3가지

### 1. Scale-Out L1 스킵 (BULL 국면)

**현황 (최근 3주 실데이터):**
- L1 매도 11건, 매도금액 5,654만원, 수익 269만원 (평균 +3.8%)
- L1 실행 후 **같은 종목의 후속 매도는 훨씬 높은 수익률 달성**:
  - 한국전력: L1 +4.0% → 후속 +8.3%
  - 영원무역: L1 +3.8% → 후속 +10.8%
  - SK텔레콤: L1 +3.3% → 후속 +5.6%
  - NH투자증권: L1 +17.8% → 후속 +15.8% (전량)

**가설:**
L1에서 15%를 조기 매도하지 않으면, 해당 물량이 Trailing TP(+4~13%)까지 가면서 더 높은 수익을 낸다.
비용 절감(12만원)은 부수적.

**구현 방향:**
- `SellConfig`에 `scale_out_skip_l1_bull: bool = False` 추가
- `check_scale_out()`에서 `regime in (BULL, STRONG_BULL) and scale_out_level == 0` 시 스킵
- 또는 `SELL_SCALE_OUT_LEVELS_BULL`에서 L1 제거: `"7.0:20,15.0:25,25.0:25"`

**리스크:**
- L1 없이 바로 하락하면 수익 기회를 아예 놓칠 수 있음
- Trailing TP activation(4%) 미달 시 보호 없음 → Breakeven Stop(3%)이 커버

---

### 2. RSI Overbought 매도 재검토

**현황 (최근 3주 실데이터):**
- RSI 매도 7건, 매도금액 4,248만원, 수익 180만원 (평균 +4.3%)
- **후속 매도가 일관되게 더 높은 수익률:**
  - 신한지주: RSI +6.3% → 후속 **+13.2%**, +15.1%
  - 신세계: RSI +3.4% → 후속 **+8.3%**, +6.5%
  - CJ: RSI +4.1% → 후속 +5.3%
  - 포스코인터내셔널: RSI +6.5% → 후속 +5.9%

**핵심:**
RSI 75에서 50%를 팔았지만, 나머지 50%는 +8~15%까지 올라갔다.
주도주는 RSI 75-85에서 한참 더 올라가는 경우가 많다.

**구현 방향 (3가지 중 택1):**

| 옵션 | 변경 | 효과 |
|------|------|------|
| A. BULL에서 threshold 상향 | `rsi_overbought_threshold` 75→85 (BULL만) | 보수적 |
| B. Trailing TP 활성 시 RSI 매도 비활성화 | `check_rsi_overbought()`에서 `high_profit_pct >= trailing_activation` 시 스킵 | 중간 |
| C. RSI 매도 자체 비활성화 | `SellConfig.rsi_sell_enabled: bool` | 급진적 |

**권장:** 옵션 B. Trailing TP가 이미 활성화된 포지션은 Trailing이 알아서 관리하므로 RSI 매도가 불필요.

**리스크:**
- RSI 85+ 과열 구간에서 급락 시 절반이라도 먼저 빼는 안전장치가 사라짐
- Hard Stop(-10%)과 Breakeven Stop(+3% 도달 후 +0.3%)이 안전망으로 작동

---

### 3. Death Cross 매도 효용 검증

**현황 (최근 3주 실데이터):**
- 1건, 매도금액 2,876만원, **-131만원 손실** (profit_pct -4.4%)
- Death Cross는 "손실 중 포지션 조기 정리" 목적이지만, -4.4%면 Fixed Stop(-7%)보다 일찍 잘라준 것
- 다만 3주 1건으로 샘플 부족 — 추가 모니터링 필요

**구현 방향:**
- BULL에서 Death Cross 비활성화: `check_death_cross()`에 regime 파라미터 추가
- SIDEWAYS/BEAR에서만 활성: 하락 추세에서는 데드크로스가 유의미한 신호
- `SellConfig.death_cross_bear_only: bool = False` 추가

**리스크:**
- BULL에서도 개별 종목 급락 시 데드크로스가 유일한 조기 경보일 수 있음
- 다만 ATR Stop, Breakeven Stop, Time-Tightening이 이미 커버

---

## 구현 순서 및 진행 상황

```
1. ✅ Scale-Out L0 스킵 — BULL 국면 (2026-02-22 배포)
   ↓
2. RSI Overbought 옵션 B (Trailing 활성 시 비활성화)
   ↓
3. Death Cross BULL 비활성화 (샘플 추가 축적 후 판단)
```

### 1. Scale-Out L0 스킵 완료 내역 (2026-02-22)

**변경:**
- `config.py`: `scale_out_levels_bull` = `"7.0:25,15.0:25,25.0:15"` (L0 3.0:25 제거, 4단계→3단계)
- `.env`: `SELL_SCALE_OUT_LEVELS_BULL` = `7.0:20,15.0:25,25.0:25` (운영값도 L0 제거)
- `test_exit_rules.py`: TestScaleOut 6개 수정 + 2개 신규 (9개 전체 통과)
  - `test_bull_skips_low_profit`: BULL +3.5% → 스킵 확인
  - `test_sideways_still_has_l0`: SIDEWAYS +3.0% L0 유지 확인
  - min_transaction 테스트 3개: BULL→SIDEWAYS 변경 (SIDEWAYS는 L0 유지)

**변경하지 않은 것:**
- `check_scale_out()` 함수 로직 (config만 변경)
- SIDEWAYS/BEAR config (기존 유지)

**모니터링 포인트:**
- BULL 국면에서 +3~7% 구간 물량이 Trailing TP까지 도달하는지 확인
- Breakeven Stop(+3% 도달 후 +0.3% 미만)이 하락 시 안전망 역할 수행 여부

---

## 관련 파일

| 파일 | 변경 내용 |
|------|----------|
| `prime_jennie/domain/config.py` | scale_out_levels_bull L0 제거 |
| `.env` | SELL_SCALE_OUT_LEVELS_BULL L0 제거 |
| `tests/unit/services/test_exit_rules.py` | TestScaleOut BULL 3단계 반영, 신규 2개 |

## 다음 작업

- 항목 2: RSI Overbought 옵션 B — Trailing TP 활성 시 RSI 매도 비활성화
- 항목 3: Death Cross BULL 비활성화 — 샘플 추가 축적 후 판단
- Scale-Out L0 스킵 효과 모니터링 (1주 이상)

## 참고 데이터

- 보고서: `docs/exit-rules-improvement-report.md` (섹션 10. 다음 이터레이션 검토 항목)
- DB 실증: STOP_LOSS 29건 중 12건(41.4%)이 +3% 도달 이력 (Breakeven 효과 근거)
- 거래비용 실측: 왕복 ~0.228%, 순이익의 21.6% (뱅키스 + 2026 코스피 세율)
