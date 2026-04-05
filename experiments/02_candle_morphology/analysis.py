"""실험 2: 5분봉 캔들 '체형' 시퀀스 매칭 (Candle Morphology Clustering)

급락/급등 직전에 반복적으로 나타나는 캔들 형태 시퀀스를 데이터에서 직접 도출한다.
"""

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.stats import chi2_contingency
from sklearn.cluster import DBSCAN
from sklearn.neighbors import NearestNeighbors

# ── 프로젝트 루트 설정 ──
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT_DIR = ROOT / "experiments" / "02_candle_morphology"
OUT_DIR.mkdir(parents=True, exist_ok=True)

from dotenv import load_dotenv

load_dotenv(ROOT / ".env.dev")

from sqlalchemy import create_engine, text

from prime_jennie.domain.config import DatabaseConfig


def load_minute_data(engine, stock_code: str) -> pd.DataFrame:
    """5분봉 데이터 로드."""
    query = text("""
        SELECT price_datetime, open_price, high_price, low_price, close_price, volume
        FROM stock_minute_prices
        WHERE stock_code = :code
        ORDER BY price_datetime
    """)
    df = pd.read_sql(query, engine, params={"code": stock_code})
    df["price_datetime"] = pd.to_datetime(df["price_datetime"])
    df["date"] = df["price_datetime"].dt.date
    return df


def compute_morphology_features(df: pd.DataFrame) -> pd.DataFrame:
    """캔들 형태 벡터 생성."""
    o, h, l, c = df["open_price"], df["high_price"], df["low_price"], df["close_price"]
    rng = h - l + 1e-10

    df = df.copy()
    df["body_ratio"] = np.abs(c - o) / rng
    df["upper_shadow"] = (h - np.maximum(o, c)) / rng
    df["lower_shadow"] = (np.minimum(o, c) - l) / rng
    df["direction"] = np.where(c >= o, 1, -1).astype(float)
    df["position"] = (c - l) / rng

    # 몸통 크기 (절대값)
    body_size = np.abs(c - o)
    # rolling percentile (20 window)
    df["body_size_rank"] = body_size.rolling(20, min_periods=20).apply(
        lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False
    )
    df["volume_rank"] = (
        df["volume"].rolling(20, min_periods=20).apply(lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False)
    )
    return df


def detect_surge_events(df: pd.DataFrame, threshold: float = 0.02):
    """급등/급락 이벤트 탐지: 향후 12캔들 내 +/-2% 이상."""
    dates = df["date"].unique()
    surge_up_idx = []
    surge_down_idx = []

    for d in dates:
        day_df = df[df["date"] == d].reset_index(drop=True)
        n = len(day_df)
        for i in range(n - 12):
            base_close = day_df.loc[i, "close_price"]
            future = day_df.loc[i + 1 : i + 12]
            max_high = future["high_price"].max()
            min_low = future["low_price"].min()

            if (max_high - base_close) / base_close >= threshold:
                surge_up_idx.append(day_df.loc[i, "price_datetime"])
            if (base_close - min_low) / base_close >= threshold:
                surge_down_idx.append(day_df.loc[i, "price_datetime"])

    return surge_up_idx, surge_down_idx


def extract_sequences(df: pd.DataFrame, event_times: list, seq_len: int = 6):
    """이벤트 직전 seq_len 캔들의 형태 벡터 시퀀스 추출."""
    feature_cols = [
        "body_ratio",
        "upper_shadow",
        "lower_shadow",
        "direction",
        "body_size_rank",
        "volume_rank",
        "position",
    ]
    sequences = []
    valid_times = []

    df_indexed = df.set_index("price_datetime")

    for t in event_times:
        day = t.date() if hasattr(t, "date") else pd.Timestamp(t).date()
        day_data = df_indexed[df_indexed["date"] == day]
        pos = day_data.index.get_loc(t) if t in day_data.index else None
        if pos is None or pos < seq_len:
            continue
        seq = day_data.iloc[pos - seq_len : pos][feature_cols].values
        if np.isnan(seq).any():
            continue
        sequences.append(seq.flatten())
        valid_times.append(t)

    return np.array(sequences) if sequences else np.empty((0, seq_len * len(feature_cols))), valid_times


def compute_post_returns(df: pd.DataFrame, event_times: list, horizons: list[int]):
    """이벤트 이후 수익률 계산."""
    df_indexed = df.set_index("price_datetime")
    results = []
    for t in event_times:
        day = t.date() if hasattr(t, "date") else pd.Timestamp(t).date()
        day_data = df_indexed[df_indexed["date"] == day]
        if t not in day_data.index:
            continue
        pos = day_data.index.get_loc(t)
        base = day_data.iloc[pos]["close_price"]
        row = {"event_time": t}
        for h in horizons:
            if pos + h < len(day_data):
                row[f"ret_T{h}"] = (day_data.iloc[pos + h]["close_price"] - base) / base
            else:
                row[f"ret_T{h}"] = np.nan
        results.append(row)
    return pd.DataFrame(results)


def find_eps(X: np.ndarray, k: int = 4) -> float:
    """k-distance plot으로 DBSCAN eps 결정."""
    if len(X) < k + 1:
        return 1.0
    # 표준화 후 거리 계산
    from sklearn.preprocessing import StandardScaler

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    nn = NearestNeighbors(n_neighbors=k)
    nn.fit(X_scaled)
    distances, _ = nn.kneighbors(X_scaled)
    k_dist = np.sort(distances[:, -1])

    # 기울기 변화점(knee) 탐지 — 상위 10~90% 구간에서
    n = len(k_dist)
    start, end = int(n * 0.1), int(n * 0.9)
    diffs = np.diff(k_dist[start:end])
    if len(diffs) > 0:
        knee_idx = start + np.argmax(diffs) + 1
        eps = k_dist[knee_idx]
    else:
        eps = np.median(k_dist)

    # k-distance plot 저장
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(k_dist)
    ax.axhline(y=eps, color="r", linestyle="--", label=f"eps={eps:.3f}")
    ax.set_xlabel("Points (sorted)")
    ax.set_ylabel(f"{k}-distance")
    ax.set_title("k-Distance Plot for DBSCAN eps Selection")
    ax.legend()
    fig.savefig(OUT_DIR / "k_distance_plot.png", dpi=150, bbox_inches="tight")
    plt.close(fig)

    return eps


def run_clustering(sequences: np.ndarray, eps: float, min_samples: int = 2):
    """DBSCAN 클러스터링."""
    if len(sequences) == 0:
        return np.array([])
    from sklearn.preprocessing import StandardScaler

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(sequences)

    db = DBSCAN(eps=eps, min_samples=min_samples, metric="euclidean")
    labels = db.fit_predict(X_scaled)
    return labels


def visualize_clusters(sequences: np.ndarray, labels: np.ndarray, event_type: str):
    """클러스터별 대표 캔들 시퀀스 시각화."""
    unique_labels = sorted(set(labels) - {-1})
    if not unique_labels:
        # 클러스터 없음 — 빈 플롯
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.text(0.5, 0.5, f"No clusters found for {event_type}", ha="center", va="center", fontsize=14)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        return fig

    n_clusters = len(unique_labels)
    fig, axes = plt.subplots(1, min(n_clusters, 6), figsize=(4 * min(n_clusters, 6), 4))
    if n_clusters == 1:
        axes = [axes]

    feature_names = ["body", "upper_sh", "lower_sh", "dir", "body_rk", "vol_rk", "pos"]
    n_features = 7
    seq_len = 6

    for ax_idx, label in enumerate(unique_labels[:6]):
        ax = axes[ax_idx]
        cluster_seqs = sequences[labels == label]
        mean_seq = cluster_seqs.mean(axis=0).reshape(seq_len, n_features)

        # 히트맵으로 표현
        im = ax.imshow(mean_seq.T, aspect="auto", cmap="RdYlGn")
        ax.set_yticks(range(n_features))
        ax.set_yticklabels(feature_names, fontsize=7)
        ax.set_xticks(range(seq_len))
        ax.set_xticklabels([f"T-{seq_len - i}" for i in range(seq_len)], fontsize=7)
        ax.set_title(f"Cluster {label}\n(n={len(cluster_seqs)})", fontsize=9)

    fig.suptitle(f"Candle Morphology Clusters — {event_type} Events", fontsize=12)
    fig.tight_layout()
    return fig


def main():
    db_cfg = DatabaseConfig()
    engine = create_engine(db_cfg.url)

    stocks = {"005930": "삼성전자", "000660": "SK하이닉스"}
    all_surge_up_seqs = []
    all_surge_down_seqs = []
    all_random_seqs = []
    all_up_returns = []
    all_down_returns = []

    for code, name in stocks.items():
        print(f"\n{'=' * 60}")
        print(f"Processing {name} ({code})")
        print(f"{'=' * 60}")

        df = load_minute_data(engine, code)
        print(f"  Loaded {len(df):,} rows ({df['date'].nunique()} trading days)")

        df = compute_morphology_features(df)
        df_clean = df.dropna(subset=["body_size_rank", "volume_rank"]).copy()
        print(f"  After feature computation: {len(df_clean):,} rows")

        # 급변 이벤트 탐지
        surge_up, surge_down = detect_surge_events(df_clean)
        print(f"  Surge UP events: {len(surge_up)}")
        print(f"  Surge DOWN events: {len(surge_down)}")

        # 시퀀스 추출
        up_seqs, up_times = extract_sequences(df_clean, surge_up)
        down_seqs, down_times = extract_sequences(df_clean, surge_down)
        print(f"  Valid UP sequences: {len(up_seqs)}")
        print(f"  Valid DOWN sequences: {len(down_seqs)}")

        # 랜덤 대조군
        np.random.seed(42)
        all_times = df_clean["price_datetime"].tolist()
        event_set = set(surge_up + surge_down)
        non_event_times = [t for t in all_times if t not in event_set]
        sample_n = min(1000, len(non_event_times))
        random_times = list(np.random.choice(non_event_times, sample_n, replace=False))
        random_seqs, _ = extract_sequences(df_clean, random_times)
        print(f"  Random control sequences: {len(random_seqs)}")

        # 후속 수익률
        up_rets = compute_post_returns(df_clean, up_times, [6, 12])
        down_rets = compute_post_returns(df_clean, down_times, [6, 12])

        all_surge_up_seqs.append(up_seqs)
        all_surge_down_seqs.append(down_seqs)
        all_random_seqs.append(random_seqs)
        all_up_returns.append(up_rets)
        all_down_returns.append(down_rets)

    # 모든 종목 합산
    combined_up = np.vstack(all_surge_up_seqs) if any(len(s) > 0 for s in all_surge_up_seqs) else np.empty((0, 42))
    combined_down = (
        np.vstack(all_surge_down_seqs) if any(len(s) > 0 for s in all_surge_down_seqs) else np.empty((0, 42))
    )
    combined_random = np.vstack(all_random_seqs) if any(len(s) > 0 for s in all_random_seqs) else np.empty((0, 42))
    combined_up_rets = pd.concat(all_up_returns, ignore_index=True) if all_up_returns else pd.DataFrame()
    combined_down_rets = pd.concat(all_down_returns, ignore_index=True) if all_down_returns else pd.DataFrame()

    print(f"\n{'=' * 60}")
    print(f"Combined: UP={len(combined_up)}, DOWN={len(combined_down)}, RANDOM={len(combined_random)}")
    print(f"{'=' * 60}")

    # ── DBSCAN 클러스터링 ──
    results_lines = []
    results_lines.append("# 실험 2: 캔들 체형 시퀀스 매칭 (Candle Morphology Clustering)\n")
    results_lines.append("**분석 대상**: 삼성전자(005930), SK하이닉스(000660)")
    results_lines.append("**데이터 기간**: 2026-02-24 ~ 2026-04-03 (~27 거래일)")
    results_lines.append("**5분봉 캔들 수**: 각 ~11,000개\n")

    cluster_stats_rows = []

    for event_type, seqs, rets_df in [
        ("SURGE_UP", combined_up, combined_up_rets),
        ("SURGE_DOWN", combined_down, combined_down_rets),
    ]:
        print(f"\n--- Clustering {event_type} ({len(seqs)} sequences) ---")
        if len(seqs) < 10:
            print("  Too few sequences for clustering, skipping")
            results_lines.append(f"\n### {event_type}: 시퀀스 {len(seqs)}개 — 클러스터링 불가 (최소 10개 필요)\n")
            continue

        eps = find_eps(seqs)
        print(f"  Selected eps={eps:.3f}")
        labels = run_clustering(seqs, eps)
        unique_labels = sorted(set(labels) - {-1})
        n_noise = np.sum(labels == -1)
        print(f"  Clusters: {len(unique_labels)}, Noise: {n_noise}")

        results_lines.append(f"\n### {event_type} 이벤트 분석")
        results_lines.append(f"- 총 시퀀스: {len(seqs)}개")
        results_lines.append(f"- DBSCAN eps={eps:.3f}, 클러스터 수: {len(unique_labels)}, 노이즈: {n_noise}")

        if unique_labels:
            for label in unique_labels:
                mask = labels == label
                cluster_size = mask.sum()
                if len(rets_df) > 0 and "ret_T6" in rets_df.columns:
                    cluster_rets = rets_df.iloc[np.where(mask)[0]] if len(rets_df) >= len(seqs) else pd.DataFrame()
                    if not cluster_rets.empty:
                        mean_ret6 = cluster_rets["ret_T6"].mean() * 100
                        mean_ret12 = (
                            cluster_rets["ret_T12"].mean() * 100 if "ret_T12" in cluster_rets.columns else np.nan
                        )
                    else:
                        mean_ret6 = np.nan
                        mean_ret12 = np.nan
                else:
                    mean_ret6 = np.nan
                    mean_ret12 = np.nan

                cluster_stats_rows.append(
                    {
                        "event_type": event_type,
                        "cluster": label,
                        "count": cluster_size,
                        "pct_of_total": cluster_size / len(seqs) * 100,
                        "mean_ret_T6_pct": mean_ret6,
                        "mean_ret_T12_pct": mean_ret12,
                    }
                )
                results_lines.append(
                    f"  - Cluster {label}: {cluster_size}개 ({cluster_size / len(seqs) * 100:.1f}%), "
                    f"T+6 평균 수익률: {mean_ret6:.3f}%"
                )

        # 시각화
        fig = visualize_clusters(seqs, labels, event_type)
        fig.savefig(OUT_DIR / f"cluster_profiles_{event_type.lower()}.png", dpi=150, bbox_inches="tight")
        plt.close(fig)

    # 합산 cluster_profiles.png
    fig_combined, axes = plt.subplots(1, 2, figsize=(14, 5))
    for ax_idx, (event_type, seqs) in enumerate([("SURGE_UP", combined_up), ("SURGE_DOWN", combined_down)]):
        ax = axes[ax_idx]
        if len(seqs) < 10:
            ax.text(0.5, 0.5, f"No data for {event_type}", ha="center", va="center")
            continue
        eps = find_eps(seqs)
        labels = run_clustering(seqs, eps)
        unique_labels = sorted(set(labels) - {-1})
        if not unique_labels:
            ax.text(0.5, 0.5, f"No clusters for {event_type}", ha="center", va="center")
            continue
        # 가장 큰 클러스터의 평균 시퀀스
        biggest = max(unique_labels, key=lambda l: np.sum(labels == l))
        mean_seq = seqs[labels == biggest].mean(axis=0).reshape(6, 7)
        feature_names = ["body", "u_shd", "l_shd", "dir", "b_rk", "v_rk", "pos"]
        im = ax.imshow(mean_seq.T, aspect="auto", cmap="RdYlGn")
        ax.set_yticks(range(7))
        ax.set_yticklabels(feature_names, fontsize=8)
        ax.set_xticks(range(6))
        ax.set_xticklabels([f"T-{6 - i}" for i in range(6)], fontsize=8)
        n_in_cluster = np.sum(labels == biggest)
        ax.set_title(f"{event_type} — Largest Cluster (n={n_in_cluster})")
        plt.colorbar(im, ax=ax, shrink=0.8)
    fig_combined.suptitle("Candle Morphology: Largest Cluster Profiles", fontsize=13)
    fig_combined.tight_layout()
    fig_combined.savefig(OUT_DIR / "cluster_profiles.png", dpi=150, bbox_inches="tight")
    plt.close(fig_combined)

    # ── 카이제곱 검정: 급변 이벤트 전 vs 랜덤 시점의 클러스터 분포 비교 ──
    results_lines.append("\n---\n")
    results_lines.append("## 카이제곱 검정 (급변 전 vs 랜덤 시점)\n")

    for event_type, event_seqs in [("SURGE_UP", combined_up), ("SURGE_DOWN", combined_down)]:
        if len(event_seqs) < 10 or len(combined_random) < 10:
            results_lines.append(f"### {event_type}: 데이터 부족으로 검정 불가\n")
            continue

        # 같은 eps/params로 event + random 합쳐서 클러스터링
        all_seqs = np.vstack([event_seqs, combined_random])
        eps = find_eps(all_seqs)
        labels_all = run_clustering(all_seqs, eps)

        labels_event = labels_all[: len(event_seqs)]
        labels_random = labels_all[len(event_seqs) :]

        unique_all = sorted(set(labels_all) - {-1})
        if len(unique_all) < 2:
            results_lines.append(f"### {event_type}: 클러스터 2개 미만 — 검정 불가\n")
            continue

        # 각 클러스터별 빈도 (event vs random)
        contingency = []
        for label in unique_all:
            event_count = np.sum(labels_event == label)
            random_count = np.sum(labels_random == label)
            contingency.append([event_count, random_count])
        contingency = np.array(contingency)

        # 0 셀 제거
        contingency = contingency[contingency.sum(axis=1) > 0]
        if contingency.shape[0] >= 2 and np.all(contingency.sum(axis=0) > 0):
            chi2, p_val, dof, expected = chi2_contingency(contingency)
            results_lines.append(f"### {event_type}")
            results_lines.append(f"- Chi-square = {chi2:.2f}, p-value = {p_val:.4f}, dof = {dof}")
            if p_val < 0.05:
                results_lines.append("- **통계적으로 유의미** (p < 0.05): 급변 전 클러스터 분포가 랜덤과 다름")
            else:
                results_lines.append("- 통계적으로 비유의 (p >= 0.05): 급변 전 패턴이 랜덤과 구분되지 않음")
            results_lines.append("")
        else:
            results_lines.append(f"### {event_type}: contingency table 불충분 (클러스터가 한쪽 그룹에만 존재)\n")

    # ── cluster_stats.csv 저장 ──
    if cluster_stats_rows:
        stats_df = pd.DataFrame(cluster_stats_rows)
        stats_df.to_csv(OUT_DIR / "cluster_stats.csv", index=False)
        print(f"\nCluster stats saved to {OUT_DIR / 'cluster_stats.csv'}")
    else:
        pd.DataFrame(
            columns=["event_type", "cluster", "count", "pct_of_total", "mean_ret_T6_pct", "mean_ret_T12_pct"]
        ).to_csv(OUT_DIR / "cluster_stats.csv", index=False)

    # ── results.md 작성 ──
    results_lines.append("\n---\n")
    results_lines.append("## 핵심 발견 (Key Findings)\n")

    n_up = len(combined_up)
    n_down = len(combined_down)
    results_lines.append(f"- 급등 이벤트(+2% 이내 1시간): {n_up}건, 급락 이벤트(-2% 이내 1시간): {n_down}건")
    results_lines.append("- DBSCAN 클러스터링을 통해 급변 직전 캔들 형태 패턴 군집을 탐색")
    if cluster_stats_rows:
        results_lines.append(f"- 총 {len(cluster_stats_rows)}개 클러스터 발견")
        for row in cluster_stats_rows:
            if not np.isnan(row["mean_ret_T6_pct"]):
                results_lines.append(
                    f"  - {row['event_type']} Cluster {row['cluster']}: "
                    f"{row['count']}건, T+30분 평균 수익률 {row['mean_ret_T6_pct']:.3f}%"
                )
    else:
        results_lines.append("- 유의미한 반복 패턴 클러스터를 발견하지 못함")

    results_lines.append("\n## 한계 및 주의사항\n")
    results_lines.append("- **데이터 기간 제한**: ~27 거래일(2026-02-24~04-03)로 통계적 강도가 낮음")
    results_lines.append("- **종목 한정**: 삼성전자, SK하이닉스 2종목만 분석. 다른 종목에서 재현 필요")
    results_lines.append("- **과적합 위험**: 데이터 부족으로 train/test 분리가 어려움")
    results_lines.append("- **시장 구조 변화**: 특정 시기(이란 리스크 등)의 패턴이 일반화 가능한지 불확실")
    results_lines.append("- DBSCAN eps 파라미터 선택이 결과에 민감 — 다른 알고리즘(HDBSCAN 등) 비교 필요")

    results_lines.append("\n## 실전 적용 가능성\n")
    if n_up + n_down < 50:
        results_lines.append(
            "**1/5** — 샘플 수 부족으로 패턴의 통계적 유의성을 확보하기 어려움. "
            "최소 6개월(~120 거래일) 데이터 축적 후 재분석 필요."
        )
    else:
        results_lines.append("**2/5** — 패턴 후보는 발견되었으나 추가 검증 필요.")

    results_lines.append("\n## 후속 작업 제안\n")
    results_lines.append("1. 5분봉 데이터 6개월 이상 축적 후 재분석 (최소 120 거래일)")
    results_lines.append("2. HDBSCAN 등 밀도 기반 클러스터링 알고리즘 비교")
    results_lines.append("3. KOSPI 200 종목 전체로 확대하여 패턴 범용성 검증")
    results_lines.append("4. 발견된 패턴의 out-of-sample 검증 (시간 순 분리)")
    results_lines.append("5. 시장 레짐(BULL/BEAR)별 패턴 차이 분석")

    results_md = "\n".join(results_lines)
    (OUT_DIR / "results.md").write_text(results_md, encoding="utf-8")
    print(f"\nResults saved to {OUT_DIR / 'results.md'}")
    print("\n" + results_md)


if __name__ == "__main__":
    main()
