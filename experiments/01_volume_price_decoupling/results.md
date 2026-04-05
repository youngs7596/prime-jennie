# Experiment 01: Volume-Price Decoupling (Silent Accumulation Detection)

## Overview
- **Period**: 2026-02-24 ~ 2026-04-03
- **Stocks**: Samsung(005930), SK Hynix(000660)
- **Data**: 1-min candles resampled to 5-min (regular hours 09:00~15:25)
- **Decoupling criteria**: price change < 0.1% AND volume > MA20 x 2.0
- **Total 5-min candles**: 4,126
- **Decoupling events**: 17
- **Event frequency**: 0.41%

## Per-Stock Event Summary

| Stock | 5-min Candles | Events | Freq | Bullish | Bearish | Doji |
|-------|---------------|--------|------|---------|---------|------|
| Samsung(005930) | 2,063 | 8 | 0.39% | 3 | 3 | 2 |
| SK Hynix(000660) | 2,063 | 9 | 0.44% | 4 | 1 | 4 |

## Statistical Test Results (Combined)

| Horizon | Ev N | Ctrl N | Ev Mean(%) | Ctrl Mean(%) | Ev Median(%) | Ctrl Median(%) | t-test p | M-W U p | Sig? |
|---------|------|--------|------------|------------|--------------|----------------|----------|---------|------|
| T+6 (30min) | 14 | 1840 | -0.2583 | -0.0310 | -0.0513 | 0.0000 | 0.4972 | 0.6946 | NO * |
| T+12 (1h) | 12 | 1694 | -0.6388 | -0.0763 | -0.4542 | 0.0000 | 0.2905 | 0.1714 | NO * |
| T+36 (3h) | 8 | 1035 | -0.1704 | -0.1554 | -0.7286 | -0.1064 | 0.9891 | 0.8114 | NO * |
| T+next_open | 17 | 1922 | 0.1102 | -0.2091 | -0.2501 | -0.1656 | 0.6979 | 0.6687 | NO * |

> * Event count < 30: results are **reference-only**.

## Post-Event Positive Return Ratio

| Horizon | Event | Control |
|---------|-------|---------|
| T+6 (30min) | 42.9% | 46.6% |
| T+12 (1h) | 25.0% | 48.3% |
| T+36 (3h) | 37.5% | 46.6% |
| T+next_open | 47.1% | 43.5% |

## Directional Analysis (Bullish vs Bearish Decoupling)

| Horizon | Bull N | Bull Mean(%) | Bull +ratio | Bear N | Bear Mean(%) | Bear +ratio | Dir p-val |
|---------|--------|-------------|-------------|--------|-------------|-------------|-----------|
| T+6 (30min) | 5 | -1.0809 | 20.0% | 4 | -0.0373 | 25.0% | N/A |
| T+12 (1h) | 4 | -2.0021 | 0.0% | 4 | -0.8122 | 0.0% | N/A |
| T+36 (3h) | 4 | -0.7876 | 25.0% | 1 | -1.0851 | 0.0% | N/A |
| T+next_open | 7 | -0.4364 | 42.9% | 4 | -1.6423 | 25.0% | N/A |

## Key Findings

- **WARNING**: Total events = 17 (< 30). All results below are **reference-only**.
- Statistical significance found in 0/4 horizons tested.
- **T+6 (30min)**: No statistical significance (t-test p=0.4972, M-W p=0.6946).
- **T+12 (1h)**: No statistical significance (t-test p=0.2905, M-W p=0.1714).
- **T+36 (3h)**: No statistical significance (t-test p=0.9891, M-W p=0.8114).
- **T+next_open**: No statistical significance (t-test p=0.6979, M-W p=0.6687).

## Limitations

1. **Limited period**: ~27 trading days (~6 weeks). Insufficient market regime diversity.
2. **Only 2 stocks**: Generalization is limited.
3. **Small sample**: 17 events -- low statistical power.
4. **No multiple-testing correction**: 4 horizons tested independently (Bonferroni threshold = p < 0.0125).
5. **No transaction costs**: Round-trip cost ~0.228% not deducted.
6. **Resampling artifact**: 1-min to 5-min aggregation loses fine-grained timing info.
7. **Event clustering**: Consecutive decoupling candles may be double-counted.

## Practical Applicability

**Score: 1/5**

Insufficient sample. Rerun with >= 3 months data and more stocks.

## Next Steps

1. **Data expansion**: At least 6-12 months, KOSPI 200 stocks.
2. **Event clustering removal**: Merge consecutive events within 3 candles.
3. **Parameter sensitivity**: Sweep price threshold (0.05%-0.3%) and volume multiplier (1.5x-3.0x).
4. **Time-of-day analysis**: Compare morning (09:00-10:00) vs afternoon (14:00-15:25) events.
5. **Regime-conditional analysis**: Check if effect differs by Council regime (BULL/BEAR/SIDEWAYS).
6. **Backtest with costs**: Include 0.228% round-trip costs.
7. **Multi-factor combination**: Test synergy with Scout hybrid_score.
