"""실험 3: 수급 주체의 시간대별 행동 분석 (Temporal Supply-Demand Profiling)

5분봉 단위 외국인/기관 수급 데이터가 없으므로, 거래량(volume) 패턴을 프록시로 활용.
일별 수급 데이터와 시간대별 거래량 패턴의 교차 분석도 수행한다.
"""

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.stats import pearsonr, spearmanr, ttest_ind

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT_DIR = ROOT / "experiments" / "03_temporal_supply_demand"
OUT_DIR.mkdir(parents=True, exist_ok=True)

from dotenv import load_dotenv

load_dotenv(ROOT / ".env.dev")

from sqlalchemy import create_engine, text

from prime_jennie.domain.config import DatabaseConfig

# ── 시간대 정의 ──
TIME_BANDS = {
    "T1 (09:00-09:30)": ("09:00", "09:30"),
    "T2 (09:30-10:30)": ("09:30", "10:30"),
    "T3 (10:30-13:00)": ("10:30", "13:00"),
    "T4 (13:00-14:30)": ("13:00", "14:30"),
    "T5 (14:30-15:20)": ("14:30", "15:20"),
}


def load_data(engine, stock_code: str):
    """5분봉 + 일봉 + 수급 데이터 로드."""
    minute_q = text("""
        SELECT price_datetime, open_price, high_price, low_price, close_price, volume
        FROM stock_minute_prices
        WHERE stock_code = :code ORDER BY price_datetime
    """)
    daily_q = text("""
        SELECT price_date, open_price, close_price, volume
        FROM stock_daily_prices
        WHERE stock_code = :code AND price_date >= '2026-02-24'
        ORDER BY price_date
    """)
    investor_q = text("""
        SELECT trade_date, foreign_net_buy, institution_net_buy, individual_net_buy
        FROM stock_investor_tradings
        WHERE stock_code = :code AND trade_date >= '2026-02-24'
        ORDER BY trade_date
    """)

    minute_df = pd.read_sql(minute_q, engine, params={"code": stock_code})
    minute_df["price_datetime"] = pd.to_datetime(minute_df["price_datetime"])
    minute_df["date"] = minute_df["price_datetime"].dt.date
    minute_df["time"] = minute_df["price_datetime"].dt.time

    daily_df = pd.read_sql(daily_q, engine, params={"code": stock_code})
    daily_df["price_date"] = pd.to_datetime(daily_df["price_date"]).dt.date

    investor_df = pd.read_sql(investor_q, engine, params={"code": stock_code})
    investor_df["trade_date"] = pd.to_datetime(investor_df["trade_date"]).dt.date

    return minute_df, daily_df, investor_df


def assign_time_band(t) -> str:
    """시간을 시간대에 매핑."""
    from datetime import time

    for band_name, (start, end) in TIME_BANDS.items():
        sh, sm = map(int, start.split(":"))
        eh, em = map(int, end.split(":"))
        if time(sh, sm) <= t < time(eh, em):
            return band_name
    return "OTHER"


def compute_band_profiles(minute_df: pd.DataFrame):
    """시간대별 거래량 및 가격 변동 프로파일."""
    df = minute_df.copy()
    df["band"] = df["time"].apply(assign_time_band)
    df = df[df["band"] != "OTHER"]

    # 시간대별 일별 거래량 합계
    band_vol = df.groupby(["date", "band"])["volume"].sum().unstack(fill_value=0)
    # 일별 총 거래량
    daily_total = band_vol.sum(axis=1)
    # 비중
    band_pct = band_vol.div(daily_total, axis=0) * 100

    # 시간대별 가격 변동률 (시간대 첫 open → 마지막 close)
    band_returns = {}
    for band_name in TIME_BANDS:
        band_data = df[df["band"] == band_name]
        day_returns = []
        for d in band_data["date"].unique():
            day_band = band_data[band_data["date"] == d].sort_values("price_datetime")
            if len(day_band) >= 1:
                ret = (
                    (day_band.iloc[-1]["close_price"] - day_band.iloc[0]["open_price"])
                    / day_band.iloc[0]["open_price"]
                    * 100
                )
                day_returns.append({"date": d, "return_pct": ret})
        band_returns[band_name] = pd.DataFrame(day_returns).set_index("date") if day_returns else pd.DataFrame()

    return band_vol, band_pct, band_returns


def compute_daily_return(daily_df: pd.DataFrame) -> pd.Series:
    """일별 수익률."""
    ret = pd.Series(
        (daily_df["close_price"].values - daily_df["open_price"].values) / daily_df["open_price"].values * 100,
        index=daily_df["price_date"],
        name="daily_return_pct",
    )
    return ret


def main():
    db_cfg = DatabaseConfig()
    engine = create_engine(db_cfg.url)

    stocks = {"005930": "삼성전자", "000660": "SK하이닉스"}
    all_results = {}
    all_band_vols = {}
    all_band_pcts = {}
    all_band_rets = {}
    all_daily_rets = {}
    all_investor = {}

    for code, name in stocks.items():
        print(f"\n{'=' * 60}")
        print(f"{name} ({code})")
        print(f"{'=' * 60}")

        minute_df, daily_df, investor_df = load_data(engine, code)
        print(f"  5분봉: {len(minute_df):,} rows, {minute_df['date'].nunique()} days")
        print(f"  일봉: {len(daily_df)} rows")
        print(f"  수급: {len(investor_df)} rows")

        band_vol, band_pct, band_returns = compute_band_profiles(minute_df)
        daily_ret = compute_daily_return(daily_df)

        all_band_vols[code] = band_vol
        all_band_pcts[code] = band_pct
        all_band_rets[code] = band_returns
        all_daily_rets[code] = daily_ret
        all_investor[code] = investor_df.set_index("trade_date") if len(investor_df) > 0 else pd.DataFrame()

    # ── 분석 ──
    results_lines = []
    results_lines.append("# 실험 3: 수급 주체의 시간대별 행동 분석\n")
    results_lines.append("## 데이터 제약")
    results_lines.append("- 5분봉 단위 외국인/기관 순매수 데이터 **미보유** — 일별 합산치만 존재")
    results_lines.append("- **대안**: 5분봉 거래량을 프록시로 활용하여 시간대별 패턴 분석")
    results_lines.append("- 분석 기간: 2026-02-24 ~ 2026-04-03 (~28 거래일)\n")

    predictor_rows = []

    for code, name in stocks.items():
        results_lines.append(f"\n## {name} ({code})\n")

        band_pct = all_band_pcts[code]
        band_returns = all_band_rets[code]
        daily_ret = all_daily_rets[code]
        investor = all_investor[code]

        # Step 3: 시간대별 거래량 비중 vs 종가 수익률 상관관계
        results_lines.append("### 시간대별 거래량 비중 vs 종가 수익률\n")
        results_lines.append("| 시간대 | 평균 거래량 비중 | Pearson r (p) | Spearman rho (p) |")
        results_lines.append("|--------|----------------|--------------|-----------------|")

        for band in TIME_BANDS:
            if band not in band_pct.columns:
                continue
            common_dates = sorted(set(band_pct.index) & set(daily_ret.index))
            if len(common_dates) < 5:
                continue
            x = band_pct.loc[common_dates, band].values
            y = daily_ret.loc[common_dates].values

            avg_pct = x.mean()
            pr, pp = pearsonr(x, y)
            sr, sp = spearmanr(x, y)
            results_lines.append(f"| {band} | {avg_pct:.1f}% | {pr:.3f} ({pp:.3f}) | {sr:.3f} ({sp:.3f}) |")

            predictor_rows.append(
                {
                    "stock": name,
                    "band": band,
                    "metric": "vol_pct",
                    "pearson_r": pr,
                    "pearson_p": pp,
                    "spearman_r": sr,
                    "spearman_p": sp,
                    "abs_spearman": abs(sr),
                }
            )

        # Step 4: 시간대별 수익률 vs 종가 수익률
        results_lines.append("\n### 시간대별 수익률 vs 종가 수익률\n")
        results_lines.append("| 시간대 | 평균 수익률 | Pearson r (p) | Spearman rho (p) |")
        results_lines.append("|--------|-----------|--------------|-----------------|")

        for band in TIME_BANDS:
            br = band_returns.get(band)
            if br is None or br.empty:
                continue
            common_dates = sorted(set(br.index) & set(daily_ret.index))
            if len(common_dates) < 5:
                continue
            x = br.loc[common_dates, "return_pct"].values
            y = daily_ret.loc[common_dates].values

            avg_ret = x.mean()
            pr, pp = pearsonr(x, y)
            sr, sp = spearmanr(x, y)
            results_lines.append(f"| {band} | {avg_ret:+.3f}% | {pr:.3f} ({pp:.3f}) | {sr:.3f} ({sp:.3f}) |")

            predictor_rows.append(
                {
                    "stock": name,
                    "band": band,
                    "metric": "band_return",
                    "pearson_r": pr,
                    "pearson_p": pp,
                    "spearman_r": sr,
                    "spearman_p": sp,
                    "abs_spearman": abs(sr),
                }
            )

        # Step 3 확장: 조건부 확률
        results_lines.append("\n### 조건부 확률 분석\n")
        for band in TIME_BANDS:
            if band not in band_pct.columns:
                continue
            common_dates = sorted(set(band_pct.index) & set(daily_ret.index))
            if len(common_dates) < 10:
                continue
            x = band_pct.loc[common_dates, band]
            y = daily_ret.loc[common_dates]
            median_vol = x.median()
            high_vol_days = [d for d in common_dates if x.loc[d] > median_vol]
            low_vol_days = [d for d in common_dates if x.loc[d] <= median_vol]

            if high_vol_days:
                p_pos_high = (y.loc[high_vol_days] > 0).mean() * 100
            else:
                p_pos_high = np.nan
            if low_vol_days:
                p_pos_low = (y.loc[low_vol_days] > 0).mean() * 100
            else:
                p_pos_low = np.nan

            results_lines.append(
                f"- {band}: 거래량 상위 50% → 양봉 확률 {p_pos_high:.0f}%, 하위 50% → 양봉 확률 {p_pos_low:.0f}%"
            )

        # Step 5: 수급 교차 분석
        if not investor.empty and len(investor) >= 10:
            results_lines.append("\n### 수급-거래량 패턴 교차 분석\n")
            band_vol = all_band_vols[code]

            for col_name, col_label in [
                ("foreign_net_buy", "외국인"),
                ("institution_net_buy", "기관"),
            ]:
                common_dates = sorted(set(investor.index) & set(band_vol.index))
                if len(common_dates) < 5:
                    continue

                buy_days = [d for d in common_dates if investor.loc[d, col_name] > 0]
                sell_days = [d for d in common_dates if investor.loc[d, col_name] < 0]

                if len(buy_days) >= 3 and len(sell_days) >= 3:
                    results_lines.append(
                        f"\n**{col_label} 순매수일({len(buy_days)}일) vs 순매도일({len(sell_days)}일) 거래량 비중**\n"
                    )
                    results_lines.append("| 시간대 | 매수일 비중 | 매도일 비중 | 차이 | t-stat (p) |")
                    results_lines.append("|--------|----------|----------|------|-----------|")

                    buy_pct = all_band_pcts[code].loc[buy_days]
                    sell_pct = all_band_pcts[code].loc[sell_days]

                    for band in TIME_BANDS:
                        if band not in buy_pct.columns:
                            continue
                        b_mean = buy_pct[band].mean()
                        s_mean = sell_pct[band].mean()
                        if len(buy_pct[band]) >= 3 and len(sell_pct[band]) >= 3:
                            t_stat, p_val = ttest_ind(buy_pct[band].dropna(), sell_pct[band].dropna())
                            sig = " *" if p_val < 0.05 else ""
                            results_lines.append(
                                f"| {band} | {b_mean:.1f}% | {s_mean:.1f}% | {b_mean - s_mean:+.1f}% | {t_stat:.2f} ({p_val:.3f}){sig} |"
                            )
                        else:
                            results_lines.append(
                                f"| {band} | {b_mean:.1f}% | {s_mean:.1f}% | {b_mean - s_mean:+.1f}% | - |"
                            )
        else:
            results_lines.append("\n### 수급 교차 분석: 데이터 부족 (수급 데이터 < 10일)\n")

    # ── 예측력 순위 ──
    if predictor_rows:
        pred_df = pd.DataFrame(predictor_rows)
        pred_df = pred_df.sort_values("abs_spearman", ascending=False)
        pred_df.to_csv(OUT_DIR / "best_predictors.csv", index=False)

        results_lines.append("\n---\n")
        results_lines.append("## 예측력 순위 (|Spearman rho| 기준, 상위 10)\n")
        results_lines.append("| 순위 | 종목 | 시간대 | 지표 | Spearman rho | p-value |")
        results_lines.append("|------|------|--------|------|-------------|---------|")
        for i, (_, row) in enumerate(pred_df.head(10).iterrows()):
            sig = " **" if row["spearman_p"] < 0.05 else ""
            results_lines.append(
                f"| {i + 1} | {row['stock']} | {row['band']} | {row['metric']} | "
                f"{row['spearman_r']:.3f} | {row['spearman_p']:.3f}{sig} |"
            )

    # ── 히트맵 시각화 ──
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    band_names = list(TIME_BANDS.keys())

    for idx, (code, name) in enumerate(stocks.items()):
        # 거래량 비중 히트맵
        ax = axes[idx, 0]
        if code in all_band_pcts and not all_band_pcts[code].empty:
            data = all_band_pcts[code][band_names].T
            im = ax.imshow(data.values, aspect="auto", cmap="YlOrRd")
            ax.set_yticks(range(len(band_names)))
            ax.set_yticklabels([b.split(" ")[0] for b in band_names], fontsize=8)
            ax.set_xlabel("Trading Day")
            ax.set_title(f"{name} — 거래량 비중 (%)")
            plt.colorbar(im, ax=ax, shrink=0.8)

        # 수익률 히트맵
        ax = axes[idx, 1]
        ret_data = []
        for band in band_names:
            br = all_band_rets[code].get(band)
            if br is not None and not br.empty:
                ret_data.append(br["return_pct"].values)
            else:
                ret_data.append(np.zeros(1))
        # 길이 맞추기
        max_len = max(len(r) for r in ret_data)
        ret_matrix = np.full((len(band_names), max_len), np.nan)
        for i, r in enumerate(ret_data):
            ret_matrix[i, : len(r)] = r
        im = ax.imshow(ret_matrix, aspect="auto", cmap="RdYlGn", vmin=-2, vmax=2)
        ax.set_yticks(range(len(band_names)))
        ax.set_yticklabels([b.split(" ")[0] for b in band_names], fontsize=8)
        ax.set_xlabel("Trading Day")
        ax.set_title(f"{name} — 시간대별 수익률 (%)")
        plt.colorbar(im, ax=ax, shrink=0.8)

    fig.suptitle("시간대별 거래량/수익률 히트맵", fontsize=14)
    fig.tight_layout()
    fig.savefig(OUT_DIR / "correlation_heatmap.png", dpi=150, bbox_inches="tight")
    plt.close(fig)

    # ── results.md 마무리 ──
    results_lines.append("\n---\n")
    results_lines.append("## 핵심 발견 (Key Findings)\n")
    if predictor_rows:
        top = pred_df.iloc[0]
        results_lines.append(
            f"1. 가장 높은 예측력: **{top['stock']} {top['band']}** "
            f"({top['metric']}, |rho|={top['abs_spearman']:.3f}, p={top['spearman_p']:.3f})"
        )
        sig_count = len(pred_df[pred_df["spearman_p"] < 0.05])
        results_lines.append(f"2. 통계적으로 유의미한 예측 변수: {sig_count}개 / {len(pred_df)}개")
    results_lines.append("3. 5분봉 단위 수급(외국인/기관) 데이터가 없어, 거래량을 프록시로 사용")
    results_lines.append("4. 거래량 비중만으로는 방향 예측에 한계가 있으며, 수급 주체 구분이 핵심")

    results_lines.append("\n## 한계 및 주의사항\n")
    results_lines.append(
        "- **핵심 한계**: 5분봉 단위 외국인/기관 순매수 데이터 부재 → 거래량 프록시는 방향성 정보 부족"
    )
    results_lines.append("- **데이터 기간**: ~28 거래일 — 통계적 검정력 약함 (최소 60거래일 권장)")
    results_lines.append("- **종목 한정**: 2종목만 분석, 일반화에 한계")
    results_lines.append("- **수급 데이터 빈도**: 일별 합산치만 사용 — 시간대별 분리 불가")

    results_lines.append("\n## 실전 적용 가능성\n")
    if predictor_rows and pred_df.iloc[0]["spearman_p"] < 0.05:
        results_lines.append(
            "**2/5** — 일부 유의미한 시간대별 패턴이 발견되었으나, "
            "핵심 데이터(5분봉 수급)가 없어 실전 적용에는 추가 데이터 확보가 필수"
        )
    else:
        results_lines.append("**1/5** — 유의미한 패턴 미발견. 5분봉 수급 데이터 확보 후 재분석 필요")

    results_lines.append("\n## 후속 작업 제안\n")
    results_lines.append(
        "1. **5분봉 수급 데이터 확보**: KIS API `FHKST01010900` (종목별 체결 데이터)에서 "
        "매수/매도 체결량 분리 가능 여부 확인"
    )
    results_lines.append("2. 네이버 금융 시간외단일가 매매동향 크롤링으로 시간대별 수급 근사치 확보 검토")
    results_lines.append("3. 데이터 기간 3개월 이상 축적 후 재분석")
    results_lines.append("4. 시간대별 VWAP 분석 추가 (가격 가중 거래량)")
    results_lines.append("5. 시장 레짐(BULL/BEAR)별 시간대 효과 분리")

    results_md = "\n".join(results_lines)
    (OUT_DIR / "results.md").write_text(results_md, encoding="utf-8")
    print(f"\nResults saved to {OUT_DIR / 'results.md'}")
    print(f"Heatmap saved to {OUT_DIR / 'correlation_heatmap.png'}")
    if predictor_rows:
        print(f"Predictors saved to {OUT_DIR / 'best_predictors.csv'}")
    print("\n" + results_md)


if __name__ == "__main__":
    main()
