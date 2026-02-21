# Exit Rules v2 — 거래비용 최적화 & 매도 규칙 정밀화

> 선행 작업: `13b039b` feat: Exit Rules 손절/익절 비대칭 해소
> 브랜치: `development`
> 우선순위: 중 (급하진 않지만 수익률 직접 영향)

---

## 배경

Exit Rules v1 개선(Breakeven Stop, Time-Tightening, Config 튜닝)을 배포하면서, 거래비용 관점의 구조적 문제가 추가로 확인되었다.

**현재 거래비용 구조:**
- 204건/3개월 = 하루 평균 3.4건 거래
- 건당 비용: 증권거래세 0.18% + 농특세 0.15% + 수수료 ~0.05% = **~0.38%**
- 총 거래비용: `204 × 10M × 0.0038 ≈ 7.7M` = **순이익 31.1M의 25%**

거래 횟수 자체를 줄이는 것이 수익률에 직접적으로 기여한다.

---

## 검토 항목 3가지

### 1. Scale-Out L1 비활성화 (BULL 국면)

**현황:**
- Scale-Out 31건, 건당 +29.4만원, 총 +9.1M
- Scale-Out L1(+3%에서 15~25% 매도)이 거래 횟수를 늘리는 주범
- L1 실행 후 나머지 물량이 Trailing TP로 넘어가면 건당 +75.7만원

**가설:**
BULL 국면에서 Scale-Out L1을 건너뛰고 Trailing TP(activation 4%)가 직접 커버하면:
- 거래 1건 감소 (수수료 절약)
- 더 많은 물량이 Trailing TP의 높은 수익률을 타게 됨

**검증 방법:**
```sql
-- Scale-Out L1 실행 건의 후속 매도 성과 조회
-- 같은 종목의 L1 이후 L2/L3/Trailing TP 건을 매칭
SELECT t1.stock_code, t1.stock_name,
       t1.profit_pct AS l1_profit,
       t2.reason AS next_reason,
       t2.profit_pct AS next_profit
FROM tradelog t1
JOIN tradelog t2 ON t1.stock_code = t2.stock_code
  AND t2.trade_timestamp > t1.trade_timestamp
  AND t2.trade_type = 'SELL'
WHERE t1.trade_type = 'SELL'
  AND t1.reason LIKE '%Scale-out L0%'
ORDER BY t1.trade_timestamp;
```

**구현 방향:**
- `SellConfig`에 `scale_out_skip_l1_bull: bool = False` 추가
- `check_scale_out()`에서 `regime in (BULL, STRONG_BULL) and scale_out_level == 0` 시 스킵
- 또는 `SELL_SCALE_OUT_LEVELS_BULL`에서 L1 제거: `"7.0:20,15.0:25,25.0:25"`

**리스크:**
- L1 없이 바로 하락하면 수익 기회를 아예 놓칠 수 있음
- Trailing TP activation(4%) 미달 시 보호 없음 → Breakeven Stop(3%)이 커버

---

### 2. RSI Overbought 매도 재검토

**현황:**
- 12건, +3.2M, 건당 +26.7만원
- 50% 부분 매도 → **나머지 50%의 후속 성과가 핵심**

**핵심 질문:**
RSI 75에서 50%를 팔았는데 그 후 더 올라서 나머지 50%가 Trailing TP로 나왔다면, 처음부터 안 팔았으면 더 벌었다.

특히 섹터 모멘텀 팩터(v2.1)로 주도주를 잡기 시작하면 — **주도주는 RSI 75-85에서 한참 더 올라가는 경우가 많다.**

**검증 방법:**
```sql
-- RSI Overbought 매도 후 같은 종목의 나머지 물량 성과
SELECT t1.stock_code, t1.stock_name, t1.profit_pct AS rsi_sell_profit,
       t2.reason AS remaining_reason, t2.profit_pct AS remaining_profit
FROM tradelog t1
JOIN tradelog t2 ON t1.stock_code = t2.stock_code
  AND t2.trade_timestamp > t1.trade_timestamp
  AND t2.trade_type = 'SELL'
WHERE t1.trade_type = 'SELL'
  AND t1.reason = 'RSI_OVERBOUGHT'
ORDER BY t1.trade_timestamp;
```

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

**현황:**
- 15건, -2.1M, 건당 -14만원 **손실**
- Death Cross는 "손실 중 포지션 조기 정리" 목적이지만, 실제로 손실을 키우고 있음

**핵심 의문:**
Death Cross가 Fixed Stop보다 먼저 발동해서 -3% 시점에 잘라준 건지, 아니면 -1~2% 시점에 너무 일찍 잘라서 반등 기회를 놓친 건지?

**검증 방법:**
```sql
-- Death Cross 매도 건의 profit_pct 분포
SELECT stock_code, stock_name, profit_pct, holding_days,
       key_metrics_json
FROM tradelog
WHERE trade_type = 'SELL'
  AND reason = 'DEATH_CROSS'
ORDER BY profit_pct;
```

분석 포인트:
- profit_pct가 -1~-2% 구간에 집중되어 있다면 → "너무 일찍 잘라서 반등 놓침"
- profit_pct가 -3~-5% 구간이면 → "Fixed Stop 전에 잘라줘서 손실 절감"

**구현 방향:**
- BULL에서 Death Cross 비활성화: `check_death_cross()`에 regime 파라미터 추가
- SIDEWAYS/BEAR에서만 활성: 하락 추세에서는 데드크로스가 유의미한 신호
- `SellConfig.death_cross_bear_only: bool = False` 추가

**리스크:**
- BULL에서도 개별 종목 급락 시 데드크로스가 유일한 조기 경보일 수 있음
- 다만 ATR Stop, Breakeven Stop, Time-Tightening이 이미 커버

---

## 구현 순서 권장

```
1. DB 검증 (3개 쿼리 실행, 건별 분석)
   ↓
2. Scale-Out L1 스킵 (가장 단순, 효과 명확)
   ↓
3. RSI Overbought 옵션 B (Trailing 활성 시 비활성화)
   ↓
4. Death Cross BULL 비활성화 (데이터 검증 후 판단)
```

## 관련 파일

| 파일 | 예상 변경 |
|------|----------|
| `prime_jennie/domain/config.py` | SellConfig 필드 2~3개 추가 |
| `prime_jennie/services/monitor/exit_rules.py` | check_scale_out, check_rsi_overbought, check_death_cross 수정 |
| `.env` | 신규 환경변수 |
| `tests/unit/services/test_exit_rules.py` | 국면별 동작 테스트 추가 |
| `docs/exit-rules-improvement-report.md` | v2 결과 반영 |

## 선행 조건

- Exit Rules v1 배포 후 **최소 1주 모니터링** (BREAKEVEN_STOP 발동 건수/성과 확인)
- v1 효과가 확인된 후 v2 진행

## 참고 데이터

- 보고서: `docs/exit-rules-improvement-report.md` (섹션 10. 다음 이터레이션 검토 항목)
- DB 실증: STOP_LOSS 29건 중 12건(41.4%)이 +3% 도달 이력 (Breakeven 효과 근거)
- 거래비용: 순이익의 ~25% (건당 ~0.38%, 204건 기준 ~7.7M)
