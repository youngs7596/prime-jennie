#!/usr/bin/env python3
"""Overextension Filter 임계값 Grid Search 최적화.

3단계:
  Phase 1 — 국면별 독립 스윕 (각 국면만 활성, 나머지 비활성)
  Phase 2 — 유효 국면 조합 전수 탐색
  Phase 3 — 최적 조합 주변 미세 조정

Usage:
    uv run python scripts/grid_search_overextension.py --start 2025-12-01 --end 2026-03-07
    uv run python scripts/grid_search_overextension.py --start 2025-12-01 --end 2026-03-07 --env .env.dev
"""

from __future__ import annotations

import argparse
import itertools
import logging
import statistics
import sys
import time
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from sqlmodel import Session

from prime_jennie.domain.enums import MarketRegime
from prime_jennie.infra.database.engine import get_engine
from prime_jennie.services.backtest.data_loader import (
    get_trading_dates,
    load_macro_days,
    load_prices,
    load_watchlists,
)
from prime_jennie.services.backtest.engine import BacktestEngine
from prime_jennie.services.backtest.metrics import calculate_metrics
from prime_jennie.services.backtest.models import BacktestConfig

# 비활성(사실상 필터 OFF) 임계값
DISABLED = 999.0

# 국면 약어
REGIME_SHORT = {
    MarketRegime.STRONG_BULL: "SB",
    MarketRegime.BULL: "BU",
    MarketRegime.SIDEWAYS: "SW",
    MarketRegime.BEAR: "BE",
    MarketRegime.STRONG_BEAR: "SBE",
}

ALL_REGIMES = [
    MarketRegime.STRONG_BULL,
    MarketRegime.BULL,
    MarketRegime.SIDEWAYS,
    MarketRegime.BEAR,
    MarketRegime.STRONG_BEAR,
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Overextension Grid Search")
    parser.add_argument("--start", required=True, help="시작일 (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="종료일 (YYYY-MM-DD)")
    parser.add_argument("--capital", type=int, default=50_000_000)
    parser.add_argument("--env", default=".env", help=".env 파일 경로")
    return parser.parse_args()


def run_backtest(
    start_date: date,
    end_date: date,
    capital: int,
    price_cache,
    watchlists,
    macro_days,
    trading_dates,
    thresholds: dict[MarketRegime, float],
) -> dict:
    """단일 백테스트 실행, 결과 요약 dict 반환."""
    config = BacktestConfig(
        start_date=start_date,
        end_date=end_date,
        initial_capital=capital,
        overextension_filter=True,
        overextension_thresholds=thresholds,
    )

    engine = BacktestEngine(
        config=config,
        price_cache=price_cache,
        watchlists=watchlists,
        macro_days=macro_days,
        trading_dates=trading_dates,
    )

    portfolio = engine.run()
    metrics = calculate_metrics(
        snapshots=portfolio.daily_snapshots,
        trade_logs=portfolio.trade_logs,
        initial_capital=capital,
    )

    # 차단 건의 5일 후 하락 비율
    blocked = engine.blocked_by_overextension
    with_fwd = [b for b in blocked if b["fwd_5d_pct"] is not None]
    decline_count = sum(1 for b in with_fwd if b["fwd_5d_pct"] < 0) if with_fwd else 0
    decline_rate = decline_count / len(with_fwd) * 100 if with_fwd else 0.0
    avg_blocked_fwd = statistics.mean([b["fwd_5d_pct"] for b in with_fwd]) if with_fwd else 0.0

    return {
        "thresholds": thresholds,
        "total_return": metrics.total_return_pct,
        "sharpe": metrics.sharpe_ratio,
        "mdd": metrics.max_drawdown_pct,
        "win_rate": metrics.win_rate_pct,
        "total_buys": metrics.total_buys,
        "profit_factor": metrics.profit_factor,
        "avg_holding_days": metrics.avg_holding_days,
        "blocked_count": len(blocked),
        "blocked_with_fwd": len(with_fwd),
        "blocked_decline_rate": decline_rate,
        "blocked_avg_fwd": avg_blocked_fwd,
    }


def make_thresholds(
    sb: float = DISABLED,
    bu: float = DISABLED,
    sw: float = DISABLED,
    be: float = DISABLED,
    sbe: float = DISABLED,
) -> dict[MarketRegime, float]:
    return {
        MarketRegime.STRONG_BULL: sb,
        MarketRegime.BULL: bu,
        MarketRegime.SIDEWAYS: sw,
        MarketRegime.BEAR: be,
        MarketRegime.STRONG_BEAR: sbe,
    }


def threshold_str(t: dict[MarketRegime, float]) -> str:
    parts = []
    for r in ALL_REGIMES:
        v = t.get(r, DISABLED)
        parts.append(f"{REGIME_SHORT[r]}={v:.0f}" if v < DISABLED else f"{REGIME_SHORT[r]}=OFF")
    return " | ".join(parts)


def print_results_table(results: list[dict], baseline: dict, title: str, top_n: int = 20) -> None:
    """결과 테이블 출력."""
    print(f"\n{'=' * 110}")
    print(f"  {title}")
    print(f"{'=' * 110}")
    print(
        f"  {'#':>3} {'수익률':>8} {'Δ수익률':>8} {'승률':>7} {'Sharpe':>7} {'MDD':>7}"
        f" {'매수':>5} {'차단':>5} {'하락률':>7} {'차단평균':>8}"
        f" {'임계값'}"
    )
    print("  " + "-" * 106)

    base_ret = baseline["total_return"]

    for i, r in enumerate(results[:top_n]):
        delta = r["total_return"] - base_ret
        sign = "+" if delta > 0 else ""
        blocked_dr = f"{r['blocked_decline_rate']:.0f}%" if r["blocked_with_fwd"] > 0 else "-"
        blocked_avg = f"{r['blocked_avg_fwd']:+.1f}%" if r["blocked_with_fwd"] > 0 else "-"
        t_str = threshold_str(r["thresholds"])

        print(
            f"  {i + 1:>3}"
            f" {r['total_return']:>+7.2f}%"
            f" {sign}{delta:>7.2f}%"
            f" {r['win_rate']:>6.1f}%"
            f" {r['sharpe']:>7.3f}"
            f" {r['mdd']:>6.2f}%"
            f" {r['total_buys']:>5}"
            f" {r['blocked_count']:>5}"
            f" {blocked_dr:>7}"
            f" {blocked_avg:>8}"
            f" {t_str}"
        )


def phase1_independent_sweep(
    start_date, end_date, capital, price_cache, watchlists, macro_days, trading_dates, baseline
):
    """Phase 1: 각 국면별 독립 스윕."""
    print("\n" + "#" * 110)
    print("  PHASE 1: 국면별 독립 스윕 (단일 국면만 활성, 나머지 비활성)")
    print("#" * 110)

    regime_grids = {
        MarketRegime.STRONG_BULL: [15, 18, 20, 22, 25, 28, 30, 35, 40],
        MarketRegime.BULL: [12, 14, 16, 18, 20, 22, 25, 28, 30],
        MarketRegime.SIDEWAYS: [10, 12, 14, 16, 18, 20, 22, 25, 28, 30, 35],
        MarketRegime.BEAR: [6, 8, 10, 12, 14, 16, 18, 20],
        MarketRegime.STRONG_BEAR: [4, 6, 8, 10, 12, 14, 16],
    }

    regime_best: dict[MarketRegime, list[dict]] = {}

    for regime in ALL_REGIMES:
        grid = regime_grids[regime]
        results = []

        for val in grid:
            thresholds = make_thresholds()  # all disabled
            thresholds[regime] = val

            r = run_backtest(
                start_date,
                end_date,
                capital,
                price_cache,
                watchlists,
                macro_days,
                trading_dates,
                thresholds,
            )
            results.append(r)

        # 수익률 기준 정렬
        results.sort(key=lambda x: x["total_return"], reverse=True)
        regime_best[regime] = results

        print_results_table(
            results,
            baseline,
            f"Phase 1 — {regime.value} 단독 스윕",
            top_n=len(results),
        )

    return regime_best


def phase2_combined_grid(
    start_date,
    end_date,
    capital,
    price_cache,
    watchlists,
    macro_days,
    trading_dates,
    baseline,
    regime_best,
):
    """Phase 2: 유효 국면 조합 전수 탐색."""
    print("\n" + "#" * 110)
    print("  PHASE 2: 유효 국면 조합 전수 탐색")
    print("#" * 110)

    # Phase 1에서 baseline보다 나은 임계값 + OFF + 추가 탐색값
    def get_candidates(regime: MarketRegime, extra: list[float] | None = None) -> list[float]:
        """Phase 1 상위 결과 + OFF + 추가값에서 후보 추출."""
        base_ret = baseline["total_return"]
        # Phase 1에서 baseline 이상인 값 추출
        good = [
            r["thresholds"][regime]
            for r in regime_best[regime]
            if r["total_return"] >= base_ret - 0.5  # baseline -0.5% 이내
        ]
        candidates = sorted(set(good + (extra or []) + [DISABLED]))
        return candidates

    # SIDEWAYS가 가장 중요하므로 세밀하게
    sw_candidates = get_candidates(MarketRegime.SIDEWAYS, [16, 18, 20, 22, 25, 28, 30, 35])
    bu_candidates = get_candidates(MarketRegime.BULL, [18, 20, 22, 25, 30])
    sb_candidates = get_candidates(MarketRegime.STRONG_BULL, [22, 25, 30, 35])
    be_candidates = get_candidates(MarketRegime.BEAR, [10, 12, 14])
    sbe_candidates = get_candidates(MarketRegime.STRONG_BEAR, [8, 10, 12])

    # 중복 제거 + 정렬
    sw_candidates = sorted(set(sw_candidates))
    bu_candidates = sorted(set(bu_candidates))
    sb_candidates = sorted(set(sb_candidates))
    be_candidates = sorted(set(be_candidates))
    sbe_candidates = sorted(set(sbe_candidates))

    total = len(sb_candidates) * len(bu_candidates) * len(sw_candidates) * len(be_candidates) * len(sbe_candidates)
    print(
        f"\n  후보 수: SB={len(sb_candidates)} × BU={len(bu_candidates)} × SW={len(sw_candidates)}"
        f" × BE={len(be_candidates)} × SBE={len(sbe_candidates)} = {total:,}개"
    )

    # 너무 많으면 BEAR/STRONG_BEAR를 고정
    if total > 5000:
        # BEAR/STRONG_BEAR는 Phase 1 최적 + OFF만
        be_top = regime_best[MarketRegime.BEAR][0]["thresholds"][MarketRegime.BEAR]
        sbe_top = regime_best[MarketRegime.STRONG_BEAR][0]["thresholds"][MarketRegime.STRONG_BEAR]
        be_candidates = sorted({be_top, DISABLED})
        sbe_candidates = sorted({sbe_top, DISABLED})
        total = len(sb_candidates) * len(bu_candidates) * len(sw_candidates) * len(be_candidates) * len(sbe_candidates)
        print(f"  → BEAR/STRONG_BEAR 축소: {total:,}개")

    results: list[dict] = []
    t0 = time.time()
    done = 0

    for sb, bu, sw, be, sbe in itertools.product(
        sb_candidates, bu_candidates, sw_candidates, be_candidates, sbe_candidates
    ):
        thresholds = make_thresholds(sb=sb, bu=bu, sw=sw, be=be, sbe=sbe)
        r = run_backtest(
            start_date,
            end_date,
            capital,
            price_cache,
            watchlists,
            macro_days,
            trading_dates,
            thresholds,
        )
        results.append(r)
        done += 1

        if done % 100 == 0:
            elapsed = time.time() - t0
            eta = elapsed / done * (total - done)
            print(f"  진행: {done}/{total} ({done / total * 100:.1f}%) | 경과: {elapsed:.0f}s | ETA: {eta:.0f}s")

    elapsed = time.time() - t0
    print(f"\n  완료: {total}개 조합, {elapsed:.1f}초")

    # 수익률 기준 정렬
    results.sort(key=lambda x: x["total_return"], reverse=True)
    print_results_table(results, baseline, "Phase 2 — 전체 조합 (수익률 기준 Top 20)")

    # Sharpe 기준 정렬
    results_sharpe = sorted(results, key=lambda x: x["sharpe"], reverse=True)
    print_results_table(results_sharpe, baseline, "Phase 2 — 전체 조합 (Sharpe 기준 Top 20)")

    # 차단 하락률 기준 (차단 건 5건 이상)
    results_decline = [r for r in results if r["blocked_with_fwd"] >= 5]
    results_decline.sort(key=lambda x: x["blocked_decline_rate"], reverse=True)
    print_results_table(results_decline, baseline, "Phase 2 — 차단 하락률 기준 (5건 이상, Top 20)")

    # 복합 점수: 수익률 개선 + Sharpe 개선 + 차단 정확도
    base_ret = baseline["total_return"]
    base_sharpe = baseline["sharpe"]

    def composite(r):
        d_ret = r["total_return"] - base_ret
        d_sharpe = r["sharpe"] - base_sharpe
        decline_bonus = max(0, r["blocked_decline_rate"] - 50) / 100  # 50% 초과분에 보너스
        # 차단 건이 너무 적으면 필터 무의미
        if r["blocked_count"] < 3:
            return d_ret  # 필터 효과 없음
        return d_ret + d_sharpe * 2.0 + decline_bonus * 1.0

    results_comp = sorted(results, key=composite, reverse=True)
    print_results_table(results_comp, baseline, "Phase 2 — 복합 점수 기준 Top 20")

    return results


def phase3_fine_tune(
    start_date,
    end_date,
    capital,
    price_cache,
    watchlists,
    macro_days,
    trading_dates,
    baseline,
    best_thresholds,
):
    """Phase 3: 최적 조합 주변 ±미세 조정."""
    print("\n" + "#" * 110)
    print("  PHASE 3: 최적 조합 주변 미세 조정 (±1~2 스텝)")
    print("#" * 110)

    best = best_thresholds
    offsets = [-2, -1, 0, 1, 2]

    regime_vals = {}
    for regime in ALL_REGIMES:
        base_val = best[regime]
        if base_val >= DISABLED:
            regime_vals[regime] = [DISABLED]
        else:
            # ±1, ±2 스텝
            vals = sorted({max(4, base_val + o) for o in offsets})
            regime_vals[regime] = vals

    total = 1
    for r in ALL_REGIMES:
        total *= len(regime_vals[r])

    print(f"\n  미세 조정: {total}개 조합")

    results: list[dict] = []
    for sb, bu, sw, be, sbe in itertools.product(
        regime_vals[MarketRegime.STRONG_BULL],
        regime_vals[MarketRegime.BULL],
        regime_vals[MarketRegime.SIDEWAYS],
        regime_vals[MarketRegime.BEAR],
        regime_vals[MarketRegime.STRONG_BEAR],
    ):
        thresholds = make_thresholds(sb=sb, bu=bu, sw=sw, be=be, sbe=sbe)
        r = run_backtest(
            start_date,
            end_date,
            capital,
            price_cache,
            watchlists,
            macro_days,
            trading_dates,
            thresholds,
        )
        results.append(r)

    results.sort(key=lambda x: x["total_return"], reverse=True)
    print_results_table(results, baseline, "Phase 3 — 미세 조정 (수익률 기준)", top_n=30)

    return results


def main() -> None:
    args = parse_args()

    # .env 로드
    env_path = Path(__file__).resolve().parent.parent / args.env
    load_dotenv(env_path)

    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    start_date = date.fromisoformat(args.start)
    end_date = date.fromisoformat(args.end)

    if start_date >= end_date:
        print("ERROR: start date must be before end date")
        sys.exit(1)

    print("=" * 110)
    print("  Overextension Filter — Grid Search 최적화")
    print("=" * 110)
    print(f"  기간: {start_date} ~ {end_date}")
    print(f"  자본금: {args.capital:,} KRW")
    print()

    # --- 데이터 로드 (1회만) ---
    db_engine = get_engine()
    with Session(db_engine) as session:
        print("  데이터 로드 중...")
        price_cache = load_prices(session, start_date, end_date, buffer_days=60)
        watchlists = load_watchlists(session, start_date, end_date)
        macro_days = load_macro_days(session, start_date, end_date)

    trading_dates = get_trading_dates(price_cache, start_date, end_date)
    print(f"  거래일: {len(trading_dates)}일, 워치리스트: {len(watchlists)}일분\n")

    if not trading_dates:
        print("ERROR: No trading dates found")
        sys.exit(1)

    # --- 국면 분포 확인 ---
    print("  국면 분포:")
    regime_counts: dict[MarketRegime, int] = {}
    for day in trading_dates:
        macro = macro_days.get(day)
        regime = macro.regime if macro else MarketRegime.SIDEWAYS
        regime_counts[regime] = regime_counts.get(regime, 0) + 1
    for regime in ALL_REGIMES:
        cnt = regime_counts.get(regime, 0)
        pct = cnt / len(trading_dates) * 100 if trading_dates else 0
        print(f"    {regime.value:<15} {cnt:>3}일 ({pct:>5.1f}%)")
    print()

    # --- Baseline (필터 OFF) ---
    print("  Baseline 백테스트 (Filter OFF)...")
    config_off = BacktestConfig(
        start_date=start_date,
        end_date=end_date,
        initial_capital=args.capital,
        overextension_filter=False,
    )
    engine_off = BacktestEngine(
        config=config_off,
        price_cache=price_cache,
        watchlists=watchlists,
        macro_days=macro_days,
        trading_dates=trading_dates,
    )
    portfolio_off = engine_off.run()
    metrics_off = calculate_metrics(
        snapshots=portfolio_off.daily_snapshots,
        trade_logs=portfolio_off.trade_logs,
        initial_capital=args.capital,
    )

    baseline = {
        "total_return": metrics_off.total_return_pct,
        "sharpe": metrics_off.sharpe_ratio,
        "mdd": metrics_off.max_drawdown_pct,
        "win_rate": metrics_off.win_rate_pct,
        "total_buys": metrics_off.total_buys,
        "profit_factor": metrics_off.profit_factor,
    }

    print(
        f"  Baseline: 수익률={baseline['total_return']:+.2f}%"
        f"  Sharpe={baseline['sharpe']:.3f}"
        f"  MDD={baseline['mdd']:.2f}%"
        f"  승률={baseline['win_rate']:.1f}%"
        f"  매수={baseline['total_buys']}건"
    )

    # --- Phase 1 ---
    t_total = time.time()
    regime_best = phase1_independent_sweep(
        start_date,
        end_date,
        args.capital,
        price_cache,
        watchlists,
        macro_days,
        trading_dates,
        baseline,
    )

    # --- Phase 2 ---
    phase2_results = phase2_combined_grid(
        start_date,
        end_date,
        args.capital,
        price_cache,
        watchlists,
        macro_days,
        trading_dates,
        baseline,
        regime_best,
    )

    # --- Phase 3 ---
    if phase2_results:
        best_t = phase2_results[0]["thresholds"]
        phase3_fine_tune(
            start_date,
            end_date,
            args.capital,
            price_cache,
            watchlists,
            macro_days,
            trading_dates,
            baseline,
            best_t,
        )

    elapsed_total = time.time() - t_total
    print(f"\n{'=' * 110}")
    print(f"  Grid Search 완료 — 총 소요시간: {elapsed_total:.1f}초 ({elapsed_total / 60:.1f}분)")
    print(f"{'=' * 110}")

    # --- 최종 추천 ---
    if phase2_results:
        best = phase2_results[0]
        print("\n  ** 최종 추천 임계값 **")
        for regime in ALL_REGIMES:
            val = best["thresholds"][regime]
            label = "OFF" if val >= DISABLED else f"{val:.0f}%"
            print(f"    {regime.value:<15} → {label}")
        print(f"\n    수익률: {best['total_return']:+.2f}% (baseline {baseline['total_return']:+.2f}%)")
        print(f"    Sharpe: {best['sharpe']:.3f} (baseline {baseline['sharpe']:.3f})")
        print(f"    승률:   {best['win_rate']:.1f}% (baseline {baseline['win_rate']:.1f}%)")
        print(f"    MDD:    {best['mdd']:.2f}% (baseline {baseline['mdd']:.2f}%)")
        if best["blocked_with_fwd"] > 0:
            print(f"    차단:   {best['blocked_count']}건 (하락률 {best['blocked_decline_rate']:.0f}%)")


if __name__ == "__main__":
    main()
