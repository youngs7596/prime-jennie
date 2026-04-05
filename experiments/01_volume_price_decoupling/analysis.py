"""
Experiment 01: Volume-Price Decoupling (Silent Accumulation Detection)

Goal: Test whether price stagnation + abnormally high volume segments
      are followed by statistically significant directional price moves.

Data: stock_minute_prices (1-min -> 5-min resample)
Stocks: Samsung(005930), SK Hynix(000660)
Period: 2026-02-24 ~ 2026-04-03
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from scipy import stats
from sqlalchemy import create_engine, text

warnings.filterwarnings("ignore", category=FutureWarning)

# -- Config -------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = Path(__file__).resolve().parent

STOCKS = {"005930": "Samsung", "000660": "SK Hynix"}
START_DATE = "2026-02-24"
END_DATE = "2026-04-03"

PRICE_CHANGE_THRESHOLD = 0.001  # |close-open|/open < 0.1%
VOLUME_MA_WINDOW = 20  # volume MA window
VOLUME_MULTIPLIER = 2.0  # MA multiplier

FORWARD_OFFSETS = {
    "T+6": 6,  # 30min
    "T+12": 12,  # 1h
    "T+36": 36,  # 3h
}

CONTROL_SAMPLES = 1000
RANDOM_SEED = 42

MARKET_OPEN = "09:00"
MARKET_CLOSE = "15:25"


# -- DB -----------------------------------------------------------
def get_engine():
    env_path = PROJECT_ROOT / ".env.dev"
    if env_path.exists():
        load_dotenv(env_path)
    else:
        load_dotenv(PROJECT_ROOT / ".env")
    sys.path.insert(0, str(PROJECT_ROOT))
    from prime_jennie.domain.config import DatabaseConfig

    db = DatabaseConfig()
    return create_engine(db.url)


# -- Data loading & resampling ------------------------------------
def load_minute_data(engine, stock_code: str) -> pd.DataFrame:
    query = text("""
        SELECT price_datetime, open_price, high_price, low_price, close_price, volume
        FROM stock_minute_prices
        WHERE stock_code = :code
          AND price_datetime >= :start
          AND price_datetime <= :end
        ORDER BY price_datetime
    """)
    with engine.connect() as conn:
        df = pd.read_sql(
            query,
            conn,
            params={
                "code": stock_code,
                "start": START_DATE,
                "end": END_DATE + " 23:59:59",
            },
        )
    df["price_datetime"] = pd.to_datetime(df["price_datetime"])
    return df


def filter_regular_hours(df: pd.DataFrame) -> pd.DataFrame:
    mask = (df["price_datetime"].dt.time >= pd.Timestamp("09:00").time()) & (
        df["price_datetime"].dt.time < pd.Timestamp("15:30").time()
    )
    return df[mask].copy()


def resample_to_5min(df: pd.DataFrame) -> pd.DataFrame:
    results = []
    for _dt, group in df.groupby(df["price_datetime"].dt.date):
        group = group.set_index("price_datetime").sort_index()
        ohlcv = (
            group.resample("5min", label="left", closed="left")
            .agg(
                {
                    "open_price": "first",
                    "high_price": "max",
                    "low_price": "min",
                    "close_price": "last",
                    "volume": "sum",
                }
            )
            .dropna()
        )
        ohlcv = ohlcv[
            (ohlcv.index.time >= pd.Timestamp(MARKET_OPEN).time())
            & (ohlcv.index.time <= pd.Timestamp(MARKET_CLOSE).time())
        ]
        results.append(ohlcv)

    combined = pd.concat(results).sort_index()
    combined = combined.reset_index().rename(columns={"price_datetime": "datetime"})
    combined["date"] = combined["datetime"].dt.date
    return combined


# -- Event detection -----------------------------------------------
def detect_decoupling_events(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["price_change_pct"] = (df["close_price"] - df["open_price"]).abs() / df["open_price"]
    df["vol_ma20"] = df["volume"].rolling(window=VOLUME_MA_WINDOW, min_periods=VOLUME_MA_WINDOW).mean()

    cond_a = df["price_change_pct"] < PRICE_CHANGE_THRESHOLD
    cond_b = df["volume"] > df["vol_ma20"] * VOLUME_MULTIPLIER
    cond_vol_positive = df["volume"] > 0

    df["is_decoupling"] = cond_a & cond_b & cond_vol_positive
    df["candle_type"] = np.where(
        df["close_price"] > df["open_price"],
        "bullish",
        np.where(df["close_price"] < df["open_price"], "bearish", "doji"),
    )
    return df


# -- Forward returns -----------------------------------------------
def compute_forward_returns(df: pd.DataFrame, event_indices: np.ndarray) -> pd.DataFrame:
    records = []
    dates = df["date"].values
    close_prices = df["close_price"].values

    first_candle = df.groupby("date").first().reset_index()
    next_open_map = {}
    unique_dates = sorted(df["date"].unique())
    for i, d in enumerate(unique_dates):
        if i + 1 < len(unique_dates):
            next_d = unique_dates[i + 1]
            row = first_candle[first_candle["date"] == next_d]
            if not row.empty:
                next_open_map[d] = row["open_price"].iloc[0]

    for idx in event_indices:
        event_date = dates[idx]
        event_close = close_prices[idx]
        event_dt = pd.Timestamp(df.iloc[idx]["datetime"])

        record = {
            "event_idx": idx,
            "datetime": event_dt,
            "date": event_date,
            "close_price": event_close,
            "candle_type": df.iloc[idx]["candle_type"],
            "volume": df.iloc[idx]["volume"],
            "vol_ma20": df.iloc[idx]["vol_ma20"],
            "volume_ratio": (
                df.iloc[idx]["volume"] / df.iloc[idx]["vol_ma20"] if df.iloc[idx]["vol_ma20"] > 0 else np.nan
            ),
            "price_change_pct": df.iloc[idx]["price_change_pct"],
        }

        for label, offset in FORWARD_OFFSETS.items():
            future_idx = idx + offset
            if future_idx < len(df) and dates[future_idx] == event_date:
                future_close = close_prices[future_idx]
                record[f"{label}_return"] = (future_close - event_close) / event_close
            else:
                record[f"{label}_return"] = np.nan

        if event_date in next_open_map:
            record["T+next_open_return"] = (next_open_map[event_date] - event_close) / event_close
        else:
            record["T+next_open_return"] = np.nan

        records.append(record)

    return pd.DataFrame(records)


# -- Control group --------------------------------------------------
def generate_control_group(df: pd.DataFrame, event_indices: set, n_samples: int = CONTROL_SAMPLES) -> pd.DataFrame:
    rng = np.random.default_rng(RANDOM_SEED)
    candidates = df[(df["volume"] > 0) & (df["vol_ma20"].notna()) & (~df.index.isin(event_indices))].index.values

    if len(candidates) < n_samples:
        sampled = candidates
    else:
        sampled = rng.choice(candidates, size=n_samples, replace=False)

    return compute_forward_returns(df, sampled)


# -- Statistical tests -----------------------------------------------
def statistical_tests(events_df: pd.DataFrame, control_df: pd.DataFrame) -> dict:
    return_cols = [c for c in events_df.columns if c.endswith("_return")]
    results = {}

    for col in return_cols:
        ev = events_df[col].dropna()
        ct = control_df[col].dropna()
        if len(ev) < 5 or len(ct) < 5:
            results[col] = {"note": "insufficient data"}
            continue

        t_stat, t_pval = stats.ttest_ind(ev, ct, equal_var=False)
        u_stat, u_pval = stats.mannwhitneyu(ev, ct, alternative="two-sided")

        results[col] = {
            "event_n": len(ev),
            "control_n": len(ct),
            "event_mean": ev.mean(),
            "event_std": ev.std(),
            "control_mean": ct.mean(),
            "control_std": ct.std(),
            "event_median": ev.median(),
            "control_median": ct.median(),
            "t_stat": t_stat,
            "t_pval": t_pval,
            "u_stat": u_stat,
            "u_pval": u_pval,
            "significant_t": t_pval < 0.05,
            "significant_u": u_pval < 0.05,
            "positive_ratio_event": (ev > 0).mean(),
            "positive_ratio_control": (ct > 0).mean(),
        }

    return results


# -- Directional analysis -------------------------------------------
def directional_analysis(events_df: pd.DataFrame) -> dict:
    return_cols = [c for c in events_df.columns if c.endswith("_return")]
    results = {}

    bullish = events_df[events_df["candle_type"] == "bullish"]
    bearish = events_df[events_df["candle_type"] == "bearish"]

    for col in return_cols:
        b_ret = bullish[col].dropna()
        s_ret = bearish[col].dropna()

        entry = {
            "bullish_n": len(b_ret),
            "bearish_n": len(s_ret),
            "bullish_mean": b_ret.mean() if len(b_ret) > 0 else np.nan,
            "bearish_mean": s_ret.mean() if len(s_ret) > 0 else np.nan,
            "bullish_positive_ratio": ((b_ret > 0).mean() if len(b_ret) > 0 else np.nan),
            "bearish_positive_ratio": ((s_ret > 0).mean() if len(s_ret) > 0 else np.nan),
        }

        if len(b_ret) >= 5 and len(s_ret) >= 5:
            t_stat, t_pval = stats.ttest_ind(b_ret, s_ret, equal_var=False)
            entry["direction_t_stat"] = t_stat
            entry["direction_t_pval"] = t_pval
            entry["direction_significant"] = t_pval < 0.05

        results[col] = entry

    return results


# -- Plotting --------------------------------------------------------
def plot_distributions(all_events: pd.DataFrame, all_controls: pd.DataFrame, output_path: Path):
    return_cols = ["T+6_return", "T+12_return", "T+36_return", "T+next_open_return"]
    labels = ["T+6 (30min)", "T+12 (1h)", "T+36 (3h)", "T+next_open"]

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle(
        "Volume-Price Decoupling: Event vs Control Return Distributions",
        fontsize=14,
        fontweight="bold",
    )

    for ax, col, label in zip(axes.flat, return_cols, labels):
        ev = all_events[col].dropna() * 100
        ct = all_controls[col].dropna() * 100

        if len(ev) == 0 and len(ct) == 0:
            ax.set_title(f"{label} (no data)")
            continue

        all_vals = pd.concat([ev, ct])
        lo, hi = np.percentile(all_vals, [1, 99])
        bins = np.linspace(lo, hi, 40)

        ax.hist(
            ct,
            bins=bins,
            alpha=0.5,
            label=f"Control (n={len(ct)})",
            color="steelblue",
            density=True,
        )
        ax.hist(
            ev,
            bins=bins,
            alpha=0.6,
            label=f"Event (n={len(ev)})",
            color="coral",
            density=True,
        )

        if len(ev) > 0:
            ax.axvline(
                ev.mean(),
                color="red",
                linestyle="--",
                linewidth=1.5,
                label=f"Event mean={ev.mean():.3f}%",
            )
        if len(ct) > 0:
            ax.axvline(
                ct.mean(),
                color="blue",
                linestyle="--",
                linewidth=1.5,
                label=f"Control mean={ct.mean():.3f}%",
            )

        ax.set_title(label)
        ax.set_xlabel("Return (%)")
        ax.set_ylabel("Density")
        ax.legend(fontsize=8)
        ax.xaxis.set_major_formatter(mticker.FormatStrFormatter("%.2f"))

    plt.tight_layout(rect=[0, 0, 1, 0.95])
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  -> Plot saved: {output_path}")


def plot_directional(all_events: pd.DataFrame, output_path: Path):
    return_cols = ["T+6_return", "T+12_return", "T+36_return", "T+next_open_return"]
    labels = ["T+6 (30min)", "T+12 (1h)", "T+36 (3h)", "T+next_open"]

    bullish = all_events[all_events["candle_type"] == "bullish"]
    bearish = all_events[all_events["candle_type"] == "bearish"]

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle(
        "Directional Analysis: Bullish vs Bearish Decoupling Candles",
        fontsize=14,
        fontweight="bold",
    )

    for ax, col, label in zip(axes.flat, return_cols, labels):
        b = bullish[col].dropna() * 100
        s = bearish[col].dropna() * 100

        if len(b) == 0 and len(s) == 0:
            ax.set_title(f"{label} (no data)")
            continue

        if len(b) > 0 and len(s) > 0:
            all_vals = pd.concat([b, s])
        elif len(b) > 0:
            all_vals = b
        else:
            all_vals = s
        lo, hi = np.percentile(all_vals, [1, 99]) if len(all_vals) > 1 else (-1, 1)
        bins_arr = np.linspace(lo, hi, 30)

        if len(b) > 0:
            ax.hist(
                b,
                bins=bins_arr,
                alpha=0.5,
                label=f"Bullish (n={len(b)})",
                color="green",
                density=True,
            )
        if len(s) > 0:
            ax.hist(
                s,
                bins=bins_arr,
                alpha=0.5,
                label=f"Bearish (n={len(s)})",
                color="red",
                density=True,
            )

        if len(b) > 0:
            ax.axvline(b.mean(), color="darkgreen", linestyle="--", linewidth=1.5)
        if len(s) > 0:
            ax.axvline(s.mean(), color="darkred", linestyle="--", linewidth=1.5)

        ax.set_title(label)
        ax.set_xlabel("Return (%)")
        ax.set_ylabel("Density")
        ax.legend(fontsize=8)

    plt.tight_layout(rect=[0, 0, 1, 0.95])
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  -> Plot saved: {output_path}")


# -- Write results.md ------------------------------------------------
def format_pct(val, digits=4):
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return "N/A"
    return f"{val * 100:.{digits}f}"


def write_results_md(
    stock_results: dict,
    combined_stats: dict,
    combined_dir_stats: dict,
    total_events: int,
    total_candles: int,
    output_path: Path,
):
    lines = []
    w = lines.append

    w("# Experiment 01: Volume-Price Decoupling (Silent Accumulation Detection)")
    w("")
    w("## Overview")
    w(f"- **Period**: {START_DATE} ~ {END_DATE}")
    w(f"- **Stocks**: {', '.join(f'{v}({k})' for k, v in STOCKS.items())}")
    w("- **Data**: 1-min candles resampled to 5-min (regular hours 09:00~15:25)")
    w(
        f"- **Decoupling criteria**: price change < {PRICE_CHANGE_THRESHOLD * 100:.1f}%"
        f" AND volume > MA{VOLUME_MA_WINDOW} x {VOLUME_MULTIPLIER:.1f}"
    )
    w(f"- **Total 5-min candles**: {total_candles:,}")
    w(f"- **Decoupling events**: {total_events}")
    w(f"- **Event frequency**: {total_events / total_candles * 100:.2f}%")
    w("")

    w("## Per-Stock Event Summary")
    w("")
    w("| Stock | 5-min Candles | Events | Freq | Bullish | Bearish | Doji |")
    w("|-------|---------------|--------|------|---------|---------|------|")
    for code, info in stock_results.items():
        name = STOCKS[code]
        ev = info["events_df"]
        n_candles = info["n_candles"]
        n_events = len(ev)
        bull = (ev["candle_type"] == "bullish").sum()
        bear = (ev["candle_type"] == "bearish").sum()
        doji = (ev["candle_type"] == "doji").sum()
        w(
            f"| {name}({code}) | {n_candles:,} | {n_events}"
            f" | {n_events / n_candles * 100:.2f}%"
            f" | {bull} | {bear} | {doji} |"
        )
    w("")

    return_labels = {
        "T+6_return": "T+6 (30min)",
        "T+12_return": "T+12 (1h)",
        "T+36_return": "T+36 (3h)",
        "T+next_open_return": "T+next_open",
    }

    w("## Statistical Test Results (Combined)")
    w("")
    w(
        "| Horizon | Ev N | Ctrl N | Ev Mean(%) | Ctrl Mean(%)"
        " | Ev Median(%) | Ctrl Median(%) | t-test p | M-W U p | Sig? |"
    )
    w(
        "|---------|------|--------|------------|------------"
        "|--------------|----------------|----------|---------|------|"
    )

    for col, label in return_labels.items():
        if col not in combined_stats or "note" in combined_stats[col]:
            w(f"| {label} | - | - | - | - | - | - | - | - | insufficient |")
            continue
        s = combined_stats[col]
        ref_note = " *" if s["event_n"] < 30 else ""
        sig = "YES" if (s["significant_t"] or s["significant_u"]) else "NO"
        w(
            f"| {label} | {s['event_n']} | {s['control_n']}"
            f" | {format_pct(s['event_mean'])}"
            f" | {format_pct(s['control_mean'])}"
            f" | {format_pct(s['event_median'])}"
            f" | {format_pct(s['control_median'])}"
            f" | {s['t_pval']:.4f} | {s['u_pval']:.4f}"
            f" | {sig}{ref_note} |"
        )
    w("")
    if total_events < 30:
        w("> * Event count < 30: results are **reference-only**.")
        w("")

    w("## Post-Event Positive Return Ratio")
    w("")
    w("| Horizon | Event | Control |")
    w("|---------|-------|---------|")
    for col, label in return_labels.items():
        if col in combined_stats and "positive_ratio_event" in combined_stats[col]:
            s = combined_stats[col]
            w(f"| {label} | {s['positive_ratio_event'] * 100:.1f}% | {s['positive_ratio_control'] * 100:.1f}% |")
    w("")

    w("## Directional Analysis (Bullish vs Bearish Decoupling)")
    w("")
    w("| Horizon | Bull N | Bull Mean(%) | Bull +ratio | Bear N | Bear Mean(%) | Bear +ratio | Dir p-val |")
    w("|---------|--------|-------------|-------------|--------|-------------|-------------|-----------|")
    for col, label in return_labels.items():
        if col not in combined_dir_stats:
            continue
        d = combined_dir_stats[col]
        b_mean = format_pct(d.get("bullish_mean"))
        s_mean = format_pct(d.get("bearish_mean"))
        b_pos = (
            f"{d['bullish_positive_ratio'] * 100:.1f}%"
            if not np.isnan(d.get("bullish_positive_ratio", np.nan))
            else "N/A"
        )
        s_pos = (
            f"{d['bearish_positive_ratio'] * 100:.1f}%"
            if not np.isnan(d.get("bearish_positive_ratio", np.nan))
            else "N/A"
        )
        dir_p = f"{d['direction_t_pval']:.4f}" if "direction_t_pval" in d else "N/A"
        w(f"| {label} | {d['bullish_n']} | {b_mean} | {b_pos} | {d['bearish_n']} | {s_mean} | {s_pos} | {dir_p} |")
    w("")

    # Key Findings
    w("## Key Findings")
    w("")

    sig_count = sum(
        1
        for col in return_labels
        if col in combined_stats
        and "note" not in combined_stats[col]
        and (combined_stats[col].get("significant_t", False) or combined_stats[col].get("significant_u", False))
    )
    total_tested = sum(1 for col in return_labels if col in combined_stats and "note" not in combined_stats[col])

    if total_events < 30:
        w(f"- **WARNING**: Total events = {total_events} (< 30). All results below are **reference-only**.")

    w(f"- Statistical significance found in {sig_count}/{total_tested} horizons tested.")

    for col, label in return_labels.items():
        if col not in combined_stats or "note" in combined_stats[col]:
            continue
        s = combined_stats[col]
        if s["significant_t"] or s["significant_u"]:
            direction = "positive" if s["event_mean"] > s["control_mean"] else "negative"
            w(
                f"- **{label}**: Significant difference detected"
                f" (t-test p={s['t_pval']:.4f},"
                f" M-W p={s['u_pval']:.4f})."
                f" Post-event returns tend {direction}."
            )
        else:
            w(f"- **{label}**: No statistical significance (t-test p={s['t_pval']:.4f}, M-W p={s['u_pval']:.4f}).")
    w("")

    # Limitations
    w("## Limitations")
    w("")
    w("1. **Limited period**: ~27 trading days (~6 weeks). Insufficient market regime diversity.")
    w("2. **Only 2 stocks**: Generalization is limited.")
    if total_events < 30:
        w(f"3. **Small sample**: {total_events} events -- low statistical power.")
    else:
        w(f"3. **Sample size**: {total_events} events. Sub-group analysis (bull/bear) may suffer from small samples.")
    w("4. **No multiple-testing correction**: 4 horizons tested independently (Bonferroni threshold = p < 0.0125).")
    w("5. **No transaction costs**: Round-trip cost ~0.228% not deducted.")
    w("6. **Resampling artifact**: 1-min to 5-min aggregation loses fine-grained timing info.")
    w("7. **Event clustering**: Consecutive decoupling candles may be double-counted.")
    w("")

    # Practicality score
    w("## Practical Applicability")
    w("")
    if sig_count >= 2 and total_events >= 30:
        score = 3
        comment = "Significance at multiple horizons, but needs longer data and more stocks to confirm."
    elif sig_count >= 1 and total_events >= 30:
        score = 2
        comment = "Partial significance. May serve as auxiliary filter, not standalone signal."
    elif total_events < 30:
        score = 1
        comment = "Insufficient sample. Rerun with >= 3 months data and more stocks."
    else:
        score = 1
        comment = "No significance detected. Insufficient evidence for live use."

    w(f"**Score: {score}/5**")
    w("")
    w(comment)
    w("")

    # Next steps
    w("## Next Steps")
    w("")
    w("1. **Data expansion**: At least 6-12 months, KOSPI 200 stocks.")
    w("2. **Event clustering removal**: Merge consecutive events within 3 candles.")
    w("3. **Parameter sensitivity**: Sweep price threshold (0.05%-0.3%) and volume multiplier (1.5x-3.0x).")
    w("4. **Time-of-day analysis**: Compare morning (09:00-10:00) vs afternoon (14:00-15:25) events.")
    w("5. **Regime-conditional analysis**: Check if effect differs by Council regime (BULL/BEAR/SIDEWAYS).")
    w("6. **Backtest with costs**: Include 0.228% round-trip costs.")
    w("7. **Multi-factor combination**: Test synergy with Scout hybrid_score.")
    w("")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  -> Results saved: {output_path}")


# -- Main ----------------------------------------------------------
def main():
    print("=" * 60)
    print("Experiment 01: Volume-Price Decoupling Detection")
    print("=" * 60)

    engine = get_engine()
    all_events_list = []
    all_control_list = []
    stock_results = {}
    total_candles = 0

    for code, name in STOCKS.items():
        print(f"\n[{name} ({code})]")

        print("  1. Loading 1-min data...")
        raw = load_minute_data(engine, code)
        print(f"     Raw rows: {len(raw):,}")

        print("  2. Filtering regular hours...")
        regular = filter_regular_hours(raw)
        print(f"     Regular hours rows: {len(regular):,}")

        print("  3. Resampling to 5-min candles...")
        df5 = resample_to_5min(regular)
        print(f"     5-min candles: {len(df5):,}")
        total_candles += len(df5)

        print("  4. Detecting decoupling events...")
        df5 = detect_decoupling_events(df5)
        event_mask = df5["is_decoupling"]
        event_indices = df5[event_mask].index.values
        n_events = len(event_indices)
        print(f"     Events found: {n_events}")

        print("  5. Computing forward returns for events...")
        events_df = compute_forward_returns(df5, event_indices)
        print(f"     Event returns computed: {len(events_df)}")

        print("  6. Generating control group...")
        control_df = generate_control_group(df5, set(event_indices))
        print(f"     Control samples: {len(control_df)}")

        print("  7. Statistical tests...")
        stock_stats = statistical_tests(events_df, control_df)
        for col, s in stock_stats.items():
            if "note" not in s:
                sig_label = "SIG" if s["significant_t"] or s["significant_u"] else "   "
                print(
                    f"     [{sig_label}] {col}:"
                    f" event_mean={s['event_mean'] * 100:.4f}%,"
                    f" control_mean={s['control_mean'] * 100:.4f}%,"
                    f" t_p={s['t_pval']:.4f}, u_p={s['u_pval']:.4f}"
                )

        stock_results[code] = {
            "df": df5,
            "events_df": events_df,
            "control_df": control_df,
            "stats": stock_stats,
            "n_candles": len(df5),
        }

        all_events_list.append(events_df)
        all_control_list.append(control_df)

    # -- Combined analysis --
    print("\n" + "=" * 60)
    print("Combined Analysis")
    print("=" * 60)

    all_events = pd.concat(all_events_list, ignore_index=True)
    all_controls = pd.concat(all_control_list, ignore_index=True)
    total_events = len(all_events)

    print(f"  Total events: {total_events}")
    print(f"  Total control: {len(all_controls)}")

    combined_stats = statistical_tests(all_events, all_controls)
    print("\n  [Combined Stats]")
    for col, s in combined_stats.items():
        if "note" not in s:
            sig_label = "SIG" if s["significant_t"] or s["significant_u"] else "   "
            print(
                f"  [{sig_label}] {col}:"
                f" event={s['event_mean'] * 100:.4f}% (n={s['event_n']}),"
                f" control={s['control_mean'] * 100:.4f}%"
                f" (n={s['control_n']}),"
                f" t_p={s['t_pval']:.4f}, u_p={s['u_pval']:.4f}"
            )

    combined_dir_stats = directional_analysis(all_events)
    print("\n  [Directional Analysis]")
    for col, d in combined_dir_stats.items():
        b_mean = f"{d['bullish_mean'] * 100:.4f}%" if not np.isnan(d.get("bullish_mean", np.nan)) else "N/A"
        s_mean = f"{d['bearish_mean'] * 100:.4f}%" if not np.isnan(d.get("bearish_mean", np.nan)) else "N/A"
        print(f"  {col}: bullish(n={d['bullish_n']})={b_mean}, bearish(n={d['bearish_n']})={s_mean}")

    # -- Add stock info to combined events --
    offset = 0
    for code in STOCKS:
        n = len(stock_results[code]["events_df"])
        all_events.loc[offset : offset + n - 1, "stock_code"] = code
        all_events.loc[offset : offset + n - 1, "stock_name"] = STOCKS[code]
        offset += n

    # -- Save events.csv --
    events_csv_path = OUTPUT_DIR / "events.csv"
    csv_cols = [
        "stock_code",
        "stock_name",
        "datetime",
        "date",
        "candle_type",
        "close_price",
        "volume",
        "vol_ma20",
        "volume_ratio",
        "price_change_pct",
        "T+6_return",
        "T+12_return",
        "T+36_return",
        "T+next_open_return",
    ]
    all_events[csv_cols].to_csv(events_csv_path, index=False, encoding="utf-8-sig")
    print(f"\n  -> Events CSV saved: {events_csv_path}")

    # -- Plots --
    print("\n  Generating plots...")
    plot_distributions(all_events, all_controls, OUTPUT_DIR / "distribution_plot.png")
    plot_directional(all_events, OUTPUT_DIR / "directional_plot.png")

    # -- Results MD --
    print("  Writing results.md...")
    write_results_md(
        stock_results=stock_results,
        combined_stats=combined_stats,
        combined_dir_stats=combined_dir_stats,
        total_events=total_events,
        total_candles=total_candles,
        output_path=OUTPUT_DIR / "results.md",
    )

    print("\n" + "=" * 60)
    print("Done!")
    print("=" * 60)


if __name__ == "__main__":
    main()
