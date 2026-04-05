"""
실험 4: 섹터 간 자금 이동 시차 분석 (Cross-Sector Lead-Lag Discovery)

5분봉 데이터를 이용해 섹터 간 교차 상관(cross-correlation)을 계산하고,
자금 로테이션 경로가 존재하는지 정량적으로 검증한다.

기간: 2026-02-24 ~ 2026-04-03 (~27 거래일)
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import koreanize_matplotlib  # noqa: F401 — NanumGothic 폰트 로드
import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
import pandas as pd
import pymysql
from dotenv import load_dotenv
from scipy import stats

warnings.filterwarnings("ignore", category=FutureWarning)

# ── Configuration ──────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = Path(__file__).resolve().parent

load_dotenv(PROJECT_ROOT / ".env.dev")

DB_CFG = dict(
    host=os.getenv("DB_HOST", "192.168.31.195"),
    port=int(os.getenv("DB_PORT", 3307)),
    user=os.getenv("DB_USER", "jennie"),
    password=os.getenv("DB_PASSWORD", ""),
    database=os.getenv("DB_NAME", "jennie_db_dev"),
    charset="utf8mb4",
)

# 섹터별 대표 종목 (시총 상위 + 5분봉 데이터 충분 = ~10,000+ rows)
SECTOR_STOCKS: dict[str, list[str]] = {
    "반도체/IT": ["005930", "000660"],  # 삼성전자, SK하이닉스
    "2차전지": ["373220", "006400"],  # LG에너지솔루션, 삼성SDI
    "자동차": ["005380", "000270"],  # 현대차, 기아
    "바이오": ["207940", "068270"],  # 삼성바이오, 셀트리온
    "금융": ["105560", "055550"],  # KB금융, 신한지주
    "조선/방산": ["012450", "329180"],  # 한화에어로스페이스, HD현대중공업
    "철강/소재": ["034020"],  # 두산에너빌리티 (단일 - POSCO 데이터 부족)
}

# lag 범위: -12 ~ +12 (전후 1시간, 5분봉 기준)
MAX_LAG = 12

# 급변 기준: 5분봉 수익률 -1% 이하
SHOCK_THRESHOLD = -0.01

# 한글 폰트: koreanize_matplotlib이 NanumGothic 자동 설정
plt.rcParams["axes.unicode_minus"] = False


# ── DB 유틸 ────────────────────────────────────────────────────────────


def get_connection():
    return pymysql.connect(**DB_CFG)


def load_minute_prices(stock_codes: list[str]) -> pd.DataFrame:
    """5분봉 데이터를 DB에서 로드."""
    conn = get_connection()
    placeholders = ",".join(["%s"] * len(stock_codes))
    query = f"""
        SELECT stock_code, price_datetime, close_price, volume
        FROM stock_minute_prices
        WHERE stock_code IN ({placeholders})
        ORDER BY stock_code, price_datetime
    """
    df = pd.read_sql(query, conn, params=stock_codes)
    conn.close()
    df["price_datetime"] = pd.to_datetime(df["price_datetime"])
    return df


# ── Step 1: 섹터 5분봉 수익률 시계열 ──────────────────────────────────


def build_sector_returns(raw_df: pd.DataFrame) -> pd.DataFrame:
    """
    각 섹터의 대표종목 수익률을 평균내어 섹터 수익률 시계열 생성.
    날짜 경계를 넘지 않도록 거래일 단위로 수익률 계산.
    """
    raw_df = raw_df.copy()
    raw_df["date"] = raw_df["price_datetime"].dt.date

    sector_returns = {}

    for sector, codes in SECTOR_STOCKS.items():
        stock_rets = []
        for code in codes:
            sub = raw_df[raw_df["stock_code"] == code].copy()
            sub = sub.sort_values("price_datetime")
            # 거래일별 수익률 계산 (날짜 경계 제거)
            sub["ret"] = sub.groupby("date")["close_price"].pct_change()
            sub = sub.dropna(subset=["ret"])
            stock_rets.append(sub.set_index("price_datetime")["ret"])

        if len(stock_rets) == 1:
            merged = stock_rets[0].to_frame("ret")
        else:
            merged = pd.concat(stock_rets, axis=1, join="inner")
            merged.columns = [f"ret_{i}" for i in range(len(stock_rets))]
            merged["ret"] = merged.mean(axis=1)

        sector_returns[sector] = merged["ret"]

    # 모든 섹터를 inner join -> 동일 타임스탬프만 유지
    result = pd.DataFrame(sector_returns)
    result = result.dropna()
    return result


# ── Step 2: 교차 상관 분석 ──────────────────────────────────────────────


def compute_cross_correlation_by_day(
    sector_returns: pd.DataFrame,
    sector_a: str,
    sector_b: str,
    max_lag: int = MAX_LAG,
) -> dict[int, tuple[float, float, int]]:
    """
    거래일별로 lag 교차 상관을 계산한 뒤, 전체 평균.
    날짜 경계를 넘지 않도록 각 날짜 내에서만 계산.

    Returns: {lag: (mean_corr, p_value, n_days)}
    """
    df = sector_returns[[sector_a, sector_b]].copy()
    df["date"] = df.index.date

    results: dict[int, list[float]] = {lag: [] for lag in range(-max_lag, max_lag + 1)}

    for date, group in df.groupby("date"):
        a = group[sector_a].values
        b = group[sector_b].values
        n = len(a)
        if n < max_lag + 5:  # 최소 데이터 길이
            continue

        for lag in range(-max_lag, max_lag + 1):
            if lag >= 0:
                x = a[lag:]
                y = b[: n - lag]
            else:
                x = a[: n + lag]
                y = b[-lag:]

            if len(x) < 5:
                continue
            c = np.corrcoef(x, y)[0, 1]
            if not np.isnan(c):
                results[lag].append(c)

    # Fisher z-transform으로 평균 + t-test
    summary = {}
    for lag in range(-max_lag, max_lag + 1):
        vals = results[lag]
        n = len(vals)
        if n < 3:
            summary[lag] = (np.nan, np.nan, n)
            continue
        # Fisher z-transform
        z_vals = np.arctanh(np.clip(vals, -0.999, 0.999))
        mean_z = np.mean(z_vals)
        se_z = np.std(z_vals, ddof=1) / np.sqrt(n)
        # t-test: H0: mean_z == 0
        t_stat = mean_z / se_z if se_z > 0 else 0
        p_val = 2 * (1 - stats.t.cdf(abs(t_stat), df=n - 1))
        mean_corr = np.tanh(mean_z)
        summary[lag] = (mean_corr, p_val, n)

    return summary


def compute_all_cross_correlations(
    sector_returns: pd.DataFrame,
    max_lag: int = MAX_LAG,
) -> dict[tuple[str, str], dict[int, tuple[float, float, int]]]:
    """모든 섹터 쌍의 교차 상관 계산."""
    sectors = list(sector_returns.columns)
    results = {}
    for i, sa in enumerate(sectors):
        for j, sb in enumerate(sectors):
            if i >= j:
                continue
            results[(sa, sb)] = compute_cross_correlation_by_day(sector_returns, sa, sb, max_lag)
    return results


# ── Step 3: 리드-래그 맵 ──────────────────────────────────────────────


def extract_lead_lag_map(
    cross_corrs: dict[tuple[str, str], dict[int, tuple[float, float, int]]],
    min_corr: float = 0.03,
    max_pval: float = 0.05,
) -> list[dict]:
    """
    각 섹터 쌍에서 최대 상관의 lag를 추출.
    lag > 0: sector_a가 sector_b를 리드
    lag < 0: sector_b가 sector_a를 리드
    """
    edges = []
    for (sa, sb), lag_map in cross_corrs.items():
        # lag=0 제외하고 최대 |corr| lag 찾기
        best_lag = None
        best_corr = 0
        best_pval = 1.0
        best_n = 0

        for lag, (corr, pval, n) in lag_map.items():
            if lag == 0 or np.isnan(corr):
                continue
            if abs(corr) > abs(best_corr):
                best_lag = lag
                best_corr = corr
                best_pval = pval
                best_n = n

        if best_lag is None or abs(best_corr) < min_corr:
            continue

        # lag > 0: A[lag:] vs B[:n-lag] -> A가 B를 리드
        # lag < 0: A[:n+lag] vs B[-lag:] -> B가 A를 리드
        if best_lag > 0:
            leader, follower = sa, sb
            lag_minutes = best_lag * 5
        else:
            leader, follower = sb, sa
            lag_minutes = abs(best_lag) * 5

        # lag=0 상관과 비교
        zero_corr = lag_map.get(0, (0, 1, 0))[0]
        corr_improvement = abs(best_corr) - abs(zero_corr) if not np.isnan(zero_corr) else abs(best_corr)

        edges.append(
            {
                "leader": leader,
                "follower": follower,
                "lag_bars": abs(best_lag),
                "lag_minutes": lag_minutes,
                "correlation": best_corr,
                "abs_correlation": abs(best_corr),
                "p_value": best_pval,
                "n_days": best_n,
                "corr_at_lag0": zero_corr if not np.isnan(zero_corr) else 0,
                "corr_improvement": corr_improvement,
                "significant": best_pval < max_pval and best_n >= 10,
            }
        )

    return sorted(edges, key=lambda x: x["abs_correlation"], reverse=True)


# ── Step 4: 급변 시점 분석 ────────────────────────────────────────────


def compute_shock_cross_correlation(
    sector_returns: pd.DataFrame,
    shock_sector: str = "반도체/IT",
    threshold: float = SHOCK_THRESHOLD,
    max_lag: int = MAX_LAG,
) -> dict[str, dict[int, tuple[float, float, int]]]:
    """
    shock_sector가 threshold 이하로 급락한 시점 전후에서만 교차 상관 계산.
    """
    df = sector_returns.copy()
    df["date"] = df.index.date

    # 급변 시점 식별
    shock_mask = df[shock_sector] <= threshold
    shock_dates = set(df[shock_mask].index.date)

    if len(shock_dates) < 3:
        print(f"[WARN] 급변 시점이 {len(shock_dates)}일 -- 분석 신뢰도 낮음")

    # 급변일의 데이터만 사용
    df_shock = df[df["date"].isin(shock_dates)]

    results = {}
    sectors = [s for s in sector_returns.columns if s != shock_sector]

    for target in sectors:
        lag_results: dict[int, list[float]] = {lag: [] for lag in range(-max_lag, max_lag + 1)}

        for date, group in df_shock.groupby("date"):
            a = group[shock_sector].values
            b = group[target].values
            n = len(a)
            if n < max_lag + 5:
                continue

            for lag in range(-max_lag, max_lag + 1):
                if lag >= 0:
                    x = a[lag:]
                    y = b[: n - lag]
                else:
                    x = a[: n + lag]
                    y = b[-lag:]

                if len(x) < 5:
                    continue
                c = np.corrcoef(x, y)[0, 1]
                if not np.isnan(c):
                    lag_results[lag].append(c)

        summary = {}
        for lag in range(-max_lag, max_lag + 1):
            vals = lag_results[lag]
            n = len(vals)
            if n < 3:
                summary[lag] = (np.nan, np.nan, n)
                continue
            z_vals = np.arctanh(np.clip(vals, -0.999, 0.999))
            mean_z = np.mean(z_vals)
            se_z = np.std(z_vals, ddof=1) / np.sqrt(n)
            t_stat = mean_z / se_z if se_z > 0 else 0
            p_val = 2 * (1 - stats.t.cdf(abs(t_stat), df=n - 1))
            summary[lag] = (np.tanh(mean_z), p_val, n)

        results[target] = summary

    return results


# ── Step 5: 시기별 안정성 ─────────────────────────────────────────────


def compute_split_period_correlations(
    sector_returns: pd.DataFrame,
    split_date: str = "2026-03-15",
    max_lag: int = MAX_LAG,
) -> tuple[
    dict[tuple[str, str], dict[int, tuple[float, float, int]]],
    dict[tuple[str, str], dict[int, tuple[float, float, int]]],
]:
    """전반기 / 후반기 분리 교차 상관."""
    cut = pd.Timestamp(split_date)
    first_half = sector_returns[sector_returns.index < cut]
    second_half = sector_returns[sector_returns.index >= cut]

    cc1 = compute_all_cross_correlations(first_half, max_lag)
    cc2 = compute_all_cross_correlations(second_half, max_lag)
    return cc1, cc2


# ── 시각화 ─────────────────────────────────────────────────────────────


def plot_cross_corr_matrix(
    cross_corrs: dict[tuple[str, str], dict[int, tuple[float, float, int]]],
    sectors: list[str],
    save_path: Path,
    title: str = "교차 상관 히트맵 (lag별)",
):
    """시차별 교차상관 히트맵."""
    lags = list(range(-MAX_LAG, MAX_LAG + 1))
    n_pairs = len(cross_corrs)

    pair_labels = []
    corr_matrix = np.zeros((n_pairs, len(lags)))

    for idx, ((sa, sb), lag_map) in enumerate(cross_corrs.items()):
        pair_labels.append(f"{sa} - {sb}")
        for j, lag in enumerate(lags):
            c, p, n = lag_map.get(lag, (np.nan, np.nan, 0))
            corr_matrix[idx, j] = c if not np.isnan(c) else 0

    fig, ax = plt.subplots(figsize=(16, max(6, n_pairs * 0.5 + 2)))
    im = ax.imshow(
        corr_matrix,
        aspect="auto",
        cmap="RdBu_r",
        vmin=-0.15,
        vmax=0.15,
        interpolation="nearest",
    )
    ax.set_xticks(range(len(lags)))
    ax.set_xticklabels([str(l) for l in lags], fontsize=8)
    ax.set_yticks(range(n_pairs))
    ax.set_yticklabels(pair_labels, fontsize=9)
    ax.set_xlabel("Lag (5분 단위, +: 왼쪽 섹터가 리드)")
    ax.set_title(title, fontsize=13, fontweight="bold")
    plt.colorbar(im, ax=ax, label="상관계수")

    # 최대 |corr| 위치 마킹
    for idx in range(n_pairs):
        row = corr_matrix[idx]
        nonzero = np.abs(row)
        best_j = np.argmax(nonzero)
        if nonzero[best_j] > 0.02:
            ax.plot(best_j, idx, "k*", markersize=10)

    plt.tight_layout()
    plt.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  -> {save_path}")


def plot_lead_lag_network(
    edges: list[dict],
    save_path: Path,
):
    """방향 그래프로 리드-래그 관계 시각화."""
    G = nx.DiGraph()

    # 섹터 노드 추가
    all_sectors = set()
    for e in edges:
        all_sectors.add(e["leader"])
        all_sectors.add(e["follower"])
    for s in SECTOR_STOCKS:
        all_sectors.add(s)
    for s in all_sectors:
        G.add_node(s)

    # 유의한 엣지만 추가
    sig_edges = [e for e in edges if e["significant"]]
    for e in sig_edges:
        G.add_edge(
            e["leader"],
            e["follower"],
            weight=e["abs_correlation"],
            lag=e["lag_minutes"],
            corr=e["correlation"],
        )

    fig, ax = plt.subplots(figsize=(12, 10))

    if len(G.nodes) == 0:
        ax.text(0.5, 0.5, "유의한 리드-래그 관계 없음", ha="center", va="center", fontsize=14)
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
        plt.close()
        return

    pos = nx.spring_layout(G, k=2.0, seed=42)

    # 노드
    nx.draw_networkx_nodes(
        G,
        pos,
        ax=ax,
        node_size=2000,
        node_color="lightblue",
        edgecolors="navy",
        linewidths=2,
    )
    nx.draw_networkx_labels(G, pos, ax=ax, font_size=10, font_weight="bold")

    # 엣지
    if sig_edges:
        edge_widths = [G[u][v]["weight"] * 30 for u, v in G.edges()]
        edge_colors = ["darkgreen" if G[u][v]["corr"] > 0 else "red" for u, v in G.edges()]

        nx.draw_networkx_edges(
            G,
            pos,
            ax=ax,
            width=edge_widths,
            edge_color=edge_colors,
            arrows=True,
            arrowsize=20,
            arrowstyle="-|>",
            connectionstyle="arc3,rad=0.1",
            alpha=0.7,
        )

        # 엣지 라벨
        edge_labels = {(u, v): f"{G[u][v]['lag']}분\nr={G[u][v]['corr']:.3f}" for u, v in G.edges()}
        nx.draw_networkx_edge_labels(G, pos, edge_labels=edge_labels, ax=ax, font_size=8)

    # 제목
    ax.set_title(
        "섹터 간 리드-래그 네트워크\n(화살표: 리더->팔로워, p<0.05)",
        fontsize=14,
        fontweight="bold",
    )

    # 유의하지 않은 관계 목록
    nonsig = [e for e in edges if not e["significant"]]
    if nonsig:
        note_lines = ["[참고] 비유의 관계:"]
        for e in nonsig[:5]:
            note_lines.append(
                f"  {e['leader']}->{e['follower']} "
                f"lag={e['lag_minutes']}분 r={e['correlation']:.3f} "
                f"p={e['p_value']:.3f} n={e['n_days']}"
            )
        ax.text(
            0.02,
            0.02,
            "\n".join(note_lines),
            transform=ax.transAxes,
            fontsize=7,
            verticalalignment="bottom",
            bbox=dict(boxstyle="round", facecolor="lightyellow", alpha=0.8),
        )

    ax.axis("off")
    plt.tight_layout()
    plt.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  -> {save_path}")


def plot_stability(
    cc_first: dict[tuple[str, str], dict[int, tuple[float, float, int]]],
    cc_second: dict[tuple[str, str], dict[int, tuple[float, float, int]]],
    save_path: Path,
):
    """전반기 vs 후반기 리드-래그 안정성 비교."""
    pairs = sorted(set(cc_first.keys()) | set(cc_second.keys()))
    n_pairs = len(pairs)

    fig, axes = plt.subplots(n_pairs, 1, figsize=(14, max(3 * n_pairs, 8)), squeeze=False)

    lags = list(range(-MAX_LAG, MAX_LAG + 1))

    for idx, pair in enumerate(pairs):
        ax = axes[idx, 0]
        sa, sb = pair

        # 전반기
        if pair in cc_first:
            corrs1 = [cc_first[pair].get(l, (np.nan, np.nan, 0))[0] for l in lags]
        else:
            corrs1 = [np.nan] * len(lags)

        # 후반기
        if pair in cc_second:
            corrs2 = [cc_second[pair].get(l, (np.nan, np.nan, 0))[0] for l in lags]
        else:
            corrs2 = [np.nan] * len(lags)

        ax.plot(lags, corrs1, "b-o", markersize=3, label="전반기 (2/24~3/14)", alpha=0.7)
        ax.plot(lags, corrs2, "r-s", markersize=3, label="후반기 (3/15~4/03)", alpha=0.7)
        ax.axhline(0, color="gray", linewidth=0.5, linestyle="--")
        ax.axvline(0, color="gray", linewidth=0.5, linestyle="--")
        ax.set_ylabel("상관계수")
        ax.set_title(f"{sa} - {sb}", fontsize=10, fontweight="bold")
        ax.legend(fontsize=8, loc="upper right")
        ax.set_xlim(-MAX_LAG - 0.5, MAX_LAG + 0.5)

        # best lag 표시
        for corrs, color, marker in [(corrs1, "blue", "v"), (corrs2, "red", "^")]:
            valid = [(l, c) for l, c in zip(lags, corrs) if not np.isnan(c) and l != 0]
            if valid:
                best_l, best_c = max(valid, key=lambda x: abs(x[1]))
                ax.annotate(
                    f"lag={best_l}",
                    (best_l, best_c),
                    fontsize=7,
                    color=color,
                    textcoords="offset points",
                    xytext=(5, 5),
                )

    axes[-1, 0].set_xlabel("Lag (5분 단위)")
    fig.suptitle(
        "시기별 리드-래그 안정성 비교\n전반기(2/24~3/14) vs 후반기(3/15~4/03)",
        fontsize=13,
        fontweight="bold",
        y=1.01,
    )
    plt.tight_layout()
    plt.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  -> {save_path}")


def plot_shock_analysis(
    normal_corrs: dict[tuple[str, str], dict[int, tuple[float, float, int]]],
    shock_corrs: dict[str, dict[int, tuple[float, float, int]]],
    shock_sector: str,
    save_path: Path,
):
    """급변 시점 vs 전체 교차 상관 비교 (반도체 급락 시점)."""
    targets = sorted(shock_corrs.keys())
    n_targets = len(targets)
    lags = list(range(-MAX_LAG, MAX_LAG + 1))

    fig, axes = plt.subplots(n_targets, 1, figsize=(14, max(3 * n_targets, 8)), squeeze=False)

    for idx, target in enumerate(targets):
        ax = axes[idx, 0]

        # 전체 기간
        pair_key = None
        for key in normal_corrs:
            if shock_sector in key and target in key:
                pair_key = key
                break

        if pair_key:
            sa, sb = pair_key
            flip = sa != shock_sector
            normal = []
            for l in lags:
                lookup_lag = -l if flip else l
                c, p, n = normal_corrs[pair_key].get(lookup_lag, (np.nan, np.nan, 0))
                normal.append(c)
        else:
            normal = [np.nan] * len(lags)

        # 급변 시점
        shock = [shock_corrs[target].get(l, (np.nan, np.nan, 0))[0] for l in lags]

        ax.plot(lags, normal, "b-o", markersize=3, label="전체 기간", alpha=0.7)
        ax.plot(lags, shock, "r-s", markersize=3, label=f"{shock_sector} 급락 시점", alpha=0.7)
        ax.axhline(0, color="gray", linewidth=0.5, linestyle="--")
        ax.axvline(0, color="gray", linewidth=0.5, linestyle="--")
        ax.set_ylabel("상관계수")
        ax.set_title(f"{shock_sector} -> {target}", fontsize=10, fontweight="bold")
        ax.legend(fontsize=8)
        ax.set_xlim(-MAX_LAG - 0.5, MAX_LAG + 0.5)

    axes[-1, 0].set_xlabel("Lag (5분 단위, +: 반도체가 리드)")
    fig.suptitle(
        f"급변 시점 교차 상관 강화 분석\n({shock_sector} 5분봉 수익률 <= {SHOCK_THRESHOLD * 100:.0f}%)",
        fontsize=13,
        fontweight="bold",
        y=1.01,
    )
    plt.tight_layout()
    plt.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  -> {save_path}")


# ── 결과 리포트 ────────────────────────────────────────────────────────


def generate_report(
    sector_returns: pd.DataFrame,
    edges: list[dict],
    cross_corrs: dict[tuple[str, str], dict[int, tuple[float, float, int]]],
    shock_corrs: dict[str, dict[int, tuple[float, float, int]]],
    cc_first: dict,
    cc_second: dict,
    save_path: Path,
):
    """results.md 생성."""
    lines = []
    lines.append("# 실험 4: 섹터 간 자금 이동 시차 분석 결과\n")
    lines.append(f"- 분석 일시: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"- 데이터 기간: {sector_returns.index.min()} ~ {sector_returns.index.max()}")
    lines.append(f"- 총 데이터 포인트: {len(sector_returns):,}")

    dates = sorted(set(sector_returns.index.date))
    lines.append(f"- 거래일 수: {len(dates)}")
    lines.append(f"- 섹터 수: {len(SECTOR_STOCKS)}")
    lines.append("")

    # 섹터 구성
    lines.append("## 섹터 대표종목\n")
    lines.append("| 섹터 | 종목코드 |")
    lines.append("|------|----------|")
    for sector, codes in SECTOR_STOCKS.items():
        lines.append(f"| {sector} | {', '.join(codes)} |")
    lines.append("")

    # 기초 통계
    lines.append("## 기초 통계 (5분봉 수익률)\n")
    desc = sector_returns.describe().T[["mean", "std", "min", "max"]]
    desc.columns = ["평균", "표준편차", "최소", "최대"]
    for col in desc.columns:
        desc[col] = desc[col].apply(lambda x: f"{x:.6f}")
    lines.append(desc.to_markdown())
    lines.append("")

    # 동시간 상관
    lines.append("## Lag=0 상관 행렬 (동시간)\n")
    corr0 = sector_returns.corr()
    corr0_str = corr0.copy()
    for c in corr0_str.columns:
        corr0_str[c] = corr0_str[c].apply(lambda x: f"{x:.4f}")
    lines.append(corr0_str.to_markdown())
    lines.append("")

    # -- 핵심 발견
    lines.append("## 핵심 발견 (Key Findings)\n")

    sig_edges = [e for e in edges if e["significant"]]
    nonsig_edges = [e for e in edges if not e["significant"]]

    if sig_edges:
        lines.append(f"### 유의한 리드-래그 관계 ({len(sig_edges)}건, p<0.05)\n")
        lines.append("| 리더 | 팔로워 | 시차(분) | 상관계수 | p-value | 관측일 | Lag0 대비 개선 |")
        lines.append("|------|--------|----------|----------|---------|--------|----------------|")
        for e in sig_edges:
            lines.append(
                f"| {e['leader']} | {e['follower']} | {e['lag_minutes']} | "
                f"{e['correlation']:.4f} | {e['p_value']:.4f} | {e['n_days']} | "
                f"{e['corr_improvement']:+.4f} |"
            )
        lines.append("")
    else:
        lines.append("**유의한(p<0.05) 리드-래그 관계가 발견되지 않았다.**\n")

    if nonsig_edges:
        lines.append(f"### 비유의 관계 ({len(nonsig_edges)}건, 참고용)\n")
        lines.append("| 리더 | 팔로워 | 시차(분) | 상관계수 | p-value | 관측일 |")
        lines.append("|------|--------|----------|----------|---------|--------|")
        for e in nonsig_edges[:10]:
            lines.append(
                f"| {e['leader']} | {e['follower']} | {e['lag_minutes']} | "
                f"{e['correlation']:.4f} | {e['p_value']:.4f} | {e['n_days']} |"
            )
        lines.append("")

    # 전체 쌍 최적 lag 요약
    lines.append("### 전체 섹터 쌍 최적 Lag 요약\n")
    lines.append("| 섹터 A | 섹터 B | 최적 Lag | 상관계수 | p-value |")
    lines.append("|--------|--------|----------|----------|---------|")
    for (sa, sb), lag_map in sorted(cross_corrs.items()):
        best_lag = 0
        best_corr = 0
        best_pval = 1.0
        for lag, (c, p, n) in lag_map.items():
            if np.isnan(c):
                continue
            if abs(c) > abs(best_corr):
                best_lag = lag
                best_corr = c
                best_pval = p
        lines.append(f"| {sa} | {sb} | {best_lag:+d} ({best_lag * 5:+d}분) | {best_corr:.4f} | {best_pval:.4f} |")
    lines.append("")

    # 급변 시점 분석
    lines.append("## 급변 시점 분석 (반도체/IT 급락 시)\n")

    df_temp = sector_returns.copy()
    df_temp["date"] = df_temp.index.date
    shock_mask = df_temp["반도체/IT"] <= SHOCK_THRESHOLD
    shock_dates_count = len(set(df_temp[shock_mask].index.date))
    shock_bars = shock_mask.sum()
    lines.append(f"- 급변 기준: 5분봉 수익률 <= {SHOCK_THRESHOLD * 100:.0f}%")
    lines.append(f"- 해당 거래일: {shock_dates_count}일, 총 {shock_bars}건의 급변 바")
    lines.append("")

    lines.append("| 타겟 섹터 | 최적 Lag | 급변 시 상관 | 전체 시 상관 | 강화 여부 |")
    lines.append("|-----------|----------|-------------|-------------|-----------|")
    for target, lag_map in sorted(shock_corrs.items()):
        best_lag = 0
        best_corr = 0
        for lag, (c, p, n) in lag_map.items():
            if np.isnan(c):
                continue
            if abs(c) > abs(best_corr):
                best_lag, best_corr = lag, c

        normal_corr = 0
        for key in cross_corrs:
            if "반도체/IT" in key and target in key:
                sa, sb = key
                flip = sa != "반도체/IT"
                lookup = -best_lag if flip else best_lag
                c, _, _ = cross_corrs[key].get(lookup, (0, 1, 0))
                normal_corr = c if not np.isnan(c) else 0
                break

        enhanced = "강화" if abs(best_corr) > abs(normal_corr) * 1.2 else "유사/약화"
        lines.append(
            f"| {target} | {best_lag:+d} ({best_lag * 5:+d}분) | {best_corr:.4f} | {normal_corr:.4f} | {enhanced} |"
        )
    lines.append("")

    # 시기별 안정성
    lines.append("## 시기별 안정성 분석\n")
    lines.append("전반기(2/24~3/14) vs 후반기(3/15~4/03) 최적 lag 비교:\n")
    lines.append("| 섹터 쌍 | 전반기 최적 Lag | 전반기 Corr | 후반기 최적 Lag | 후반기 Corr | 안정성 |")
    lines.append("|---------|----------------|-------------|----------------|-------------|--------|")

    for pair in sorted(set(cc_first.keys()) | set(cc_second.keys())):
        sa, sb = pair
        if pair in cc_first:
            b1_lag, b1_corr = 0, 0
            for lag, (c, p, n) in cc_first[pair].items():
                if not np.isnan(c) and abs(c) > abs(b1_corr):
                    b1_lag, b1_corr = lag, c
        else:
            b1_lag, b1_corr = None, None

        if pair in cc_second:
            b2_lag, b2_corr = 0, 0
            for lag, (c, p, n) in cc_second[pair].items():
                if not np.isnan(c) and abs(c) > abs(b2_corr):
                    b2_lag, b2_corr = lag, c
        else:
            b2_lag, b2_corr = None, None

        if b1_lag is not None and b2_lag is not None:
            same_dir = (b1_lag > 0 and b2_lag > 0) or (b1_lag < 0 and b2_lag < 0) or (b1_lag == 0 and b2_lag == 0)
            similar_mag = abs(abs(b1_lag) - abs(b2_lag)) <= 3
            if same_dir and similar_mag:
                stability = "안정"
            elif same_dir:
                stability = "방향 유지"
            else:
                stability = "불안정"
        else:
            stability = "N/A"

        b1_str = f"{b1_lag:+d}" if b1_lag is not None else "N/A"
        b1c_str = f"{b1_corr:.4f}" if b1_corr is not None else "N/A"
        b2_str = f"{b2_lag:+d}" if b2_lag is not None else "N/A"
        b2c_str = f"{b2_corr:.4f}" if b2_corr is not None else "N/A"

        lines.append(f"| {sa} - {sb} | {b1_str} | {b1c_str} | {b2_str} | {b2c_str} | {stability} |")
    lines.append("")

    # 한계 및 주의사항
    lines.append("## 한계 및 주의사항\n")
    lines.append("1. **데이터 기간이 짧다**: ~27거래일의 5분봉 데이터로, 통계적 검정력이 제한적이다.")
    lines.append(
        "2. **섹터 대표종목 편향**: ETF 대신 시총 상위 1-2종목으로 섹터를 대리하므로, 개별 종목 이벤트가 섹터 전체 움직임으로 오인될 수 있다."
    )
    lines.append("3. **철강/소재는 단일 종목(두산에너빌리티)**: 섹터 대표성이 낮다.")
    lines.append("4. **동시호가/장 초반 5분 효과**: 09:00 시가 결정 과정에서의 교란이 첫 몇 바에 영향을 줄 수 있다.")
    lines.append(
        "5. **시장 전체 베타**: 개별 섹터 간 관계가 아니라 시장 전체 방향(KOSPI)에 의한 공통 요인일 가능성이 있다. 시장 수익률 차감(잔차 분석)은 미적용."
    )
    lines.append("6. **생존자 편향**: 5분봉 데이터가 있는 대형주만 사용 -- 중소형주 섹터 움직임은 반영되지 않는다.")
    lines.append("")

    # 실전 적용 가능성
    lines.append("## 실전 적용 가능성\n")

    n_sig = len(sig_edges)
    max_corr_improvement = max((e["corr_improvement"] for e in edges), default=0)

    if n_sig >= 3 and max_corr_improvement > 0.03:
        score = 4
        comment = "다수의 유의한 리드-래그 관계가 발견됨. 추가 검증(아래 후속 작업) 후 실전 적용 가능."
    elif n_sig >= 1:
        score = 3
        comment = "일부 유의한 관계가 있으나, 상관 강도가 약하거나 시기별 불안정. 보조 지표로 활용 가능."
    elif max_corr_improvement > 0.01:
        score = 2
        comment = "통계적 유의성은 부족하지만 경향성은 관찰됨. 더 긴 데이터로 재검증 필요."
    else:
        score = 1
        comment = "5분봉 단위의 섹터 간 리드-래그 관계가 확인되지 않음. 더 긴 시간 프레임(일봉 등)으로 접근 필요."

    lines.append(f"**점수: {score}/5**\n")
    lines.append(f"{comment}\n")

    # 후속 작업
    lines.append("## 후속 작업 제안\n")
    lines.append(
        "1. **시장 베타 차감**: KOSPI 5분봉 수익률을 차감한 잔차(residual) 기반 교차 상관 분석으로 공통 요인 제거"
    )
    lines.append("2. **데이터 확장**: 최소 3개월(60거래일) 이상의 5분봉 데이터 확보 후 재분석")
    lines.append("3. **Granger 인과성 검정**: 교차 상관 외에 Granger causality test로 인과 방향 검증")
    lines.append("4. **거래량 가중**: 수익률 교차 상관에 거래량 변화율을 가중하여 실제 자금 이동 포착")
    lines.append("5. **일봉 단위 리드-래그**: 5분봉 대신 일봉으로 시간 프레임 확장 (주 단위 섹터 로테이션)")
    lines.append("6. **레짐 분리**: BULL/BEAR 국면별로 리드-래그 관계가 다른지 검증")
    lines.append("")

    with open(save_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"  -> {save_path}")


# ── Main ───────────────────────────────────────────────────────────────


def main():
    print("=" * 60)
    print("실험 4: 섹터 간 자금 이동 시차 분석")
    print("=" * 60)

    # 0. 데이터 로드
    print("\n[1/6] 5분봉 데이터 로드 중...")
    all_codes = []
    for codes in SECTOR_STOCKS.values():
        all_codes.extend(codes)
    all_codes = list(set(all_codes))
    raw_df = load_minute_prices(all_codes)
    print(f"  총 {len(raw_df):,} rows 로드 ({len(all_codes)} 종목)")

    # 1. 섹터 수익률 계산
    print("\n[2/6] 섹터 수익률 시계열 생성...")
    sector_returns = build_sector_returns(raw_df)
    print(f"  섹터 수: {len(sector_returns.columns)}")
    print(f"  타임스탬프 수: {len(sector_returns):,}")
    print(f"  기간: {sector_returns.index.min()} ~ {sector_returns.index.max()}")
    dates = sorted(set(sector_returns.index.date))
    print(f"  거래일 수: {len(dates)}")

    # 2. 교차 상관 분석
    print("\n[3/6] 교차 상관 분석 (lag -12 ~ +12)...")
    cross_corrs = compute_all_cross_correlations(sector_returns)
    print(f"  총 {len(cross_corrs)} 섹터 쌍 분석 완료")

    # 3. 리드-래그 맵
    print("\n[4/6] 리드-래그 관계 추출...")
    edges = extract_lead_lag_map(cross_corrs)
    sig_count = sum(1 for e in edges if e["significant"])
    print(f"  총 {len(edges)}건 추출, 유의(p<0.05): {sig_count}건")

    for e in edges[:5]:
        sig = "*" if e["significant"] else " "
        print(
            f"  {sig} {e['leader']:10s} -> {e['follower']:10s}  "
            f"lag={e['lag_minutes']:3d}분  r={e['correlation']:+.4f}  "
            f"p={e['p_value']:.4f}  n={e['n_days']}"
        )

    # 4. 급변 시점 분석
    print("\n[5/6] 급변 시점 교차 상관 분석 (반도체/IT 급락)...")
    shock_corrs = compute_shock_cross_correlation(sector_returns)
    for target, lag_map in sorted(shock_corrs.items()):
        best_lag, best_corr = 0, 0
        for lag, (c, p, n) in lag_map.items():
            if not np.isnan(c) and abs(c) > abs(best_corr):
                best_lag, best_corr = lag, c
        print(f"  반도체->{target:10s}  best_lag={best_lag:+3d}  r={best_corr:+.4f}")

    # 5. 시기별 안정성
    print("\n[6/6] 시기별 안정성 분석...")
    cc_first, cc_second = compute_split_period_correlations(sector_returns)

    # 시각화 생성
    print("\n시각화 생성 중...")
    sectors = list(sector_returns.columns)

    plot_cross_corr_matrix(
        cross_corrs,
        sectors,
        OUTPUT_DIR / "cross_corr_matrix.png",
    )

    plot_lead_lag_network(
        edges,
        OUTPUT_DIR / "lead_lag_network.png",
    )

    plot_stability(
        cc_first,
        cc_second,
        OUTPUT_DIR / "stability_over_time.png",
    )

    plot_shock_analysis(
        cross_corrs,
        shock_corrs,
        "반도체/IT",
        OUTPUT_DIR / "shock_analysis.png",
    )

    # 결과 보고서
    print("\n결과 보고서 생성 중...")
    generate_report(
        sector_returns,
        edges,
        cross_corrs,
        shock_corrs,
        cc_first,
        cc_second,
        OUTPUT_DIR / "results.md",
    )

    print("\n" + "=" * 60)
    print("분석 완료!")
    print(f"결과: {OUTPUT_DIR}/")
    print("=" * 60)


if __name__ == "__main__":
    main()
