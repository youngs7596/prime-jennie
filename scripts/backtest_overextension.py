#!/usr/bin/env python3
"""Overextension Filter 백테스트 비교 분석.

필터 OFF(baseline) vs ON(filtered)로 백테스트를 2회 실행하고,
차단된 케이스의 사후 수익률을 분석합니다.

Usage:
    uv run python scripts/backtest_overextension.py --start 2025-12-01 --end 2026-03-07
    uv run python scripts/backtest_overextension.py --start 2025-12-01 --end 2026-03-07 --env .env.dev
"""

from __future__ import annotations

import argparse
import logging
import statistics
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from sqlmodel import Session

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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Overextension Filter 백테스트 비교")
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
    *,
    overextension_filter: bool,
) -> tuple:
    """백테스트 1회 실행, (portfolio, metrics, engine) 반환."""
    config = BacktestConfig(
        start_date=start_date,
        end_date=end_date,
        initial_capital=capital,
        overextension_filter=overextension_filter,
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

    return portfolio, metrics, engine


def analyze_blocked(blocked: list[dict]) -> None:
    """차단된 매수의 사후 수익률 분석."""
    print("\n" + "=" * 70)
    print("  차단된 매수 사후 분석 (Overextension Filter)")
    print("=" * 70)

    if not blocked:
        print("  차단된 케이스 없음")
        return

    # 5일 후 수익률이 있는 케이스만
    with_fwd = [b for b in blocked if b["fwd_5d_pct"] is not None]

    print(f"\n  총 차단: {len(blocked)}건 (5일 후 수익률 확인 가능: {len(with_fwd)}건)")

    if not with_fwd:
        print("  (기간 말 차단 건은 5일 후 데이터 미존재)")
        return

    returns = [b["fwd_5d_pct"] for b in with_fwd]

    # 기본 통계
    avg = statistics.mean(returns)
    med = statistics.median(returns)
    neg_count = sum(1 for r in returns if r < 0)
    loss_3_count = sum(1 for r in returns if r <= -3)
    loss_5_count = sum(1 for r in returns if r <= -5)
    gain_count = sum(1 for r in returns if r > 0)

    print("\n  5일 후 수익률 분포:")
    print(f"    평균:     {avg:+.2f}%")
    print(f"    중위값:   {med:+.2f}%")
    print(f"    최소:     {min(returns):+.2f}%")
    print(f"    최대:     {max(returns):+.2f}%")
    if len(returns) > 1:
        print(f"    표준편차:  {statistics.stdev(returns):.2f}%")
    print(f"\n  하락 비율:  {neg_count}/{len(with_fwd)} ({neg_count / len(with_fwd) * 100:.1f}%)")
    print(f"  -3% 이하:  {loss_3_count}/{len(with_fwd)} ({loss_3_count / len(with_fwd) * 100:.1f}%)")
    print(f"  -5% 이하:  {loss_5_count}/{len(with_fwd)} ({loss_5_count / len(with_fwd) * 100:.1f}%)")
    print(f"  상승 비율:  {gain_count}/{len(with_fwd)} ({gain_count / len(with_fwd) * 100:.1f}%)")

    # 국면별 분석
    print(f"\n  {'국면':<15} {'건수':>5} {'평균':>8} {'중위값':>8} {'하락%':>8}")
    print("  " + "-" * 50)
    regime_groups: dict[str, list[float]] = {}
    for b in with_fwd:
        regime_name = b["regime"].value if hasattr(b["regime"], "value") else str(b["regime"])
        regime_groups.setdefault(regime_name, []).append(b["fwd_5d_pct"])

    for regime, rets in sorted(regime_groups.items()):
        r_avg = statistics.mean(rets)
        r_med = statistics.median(rets)
        r_neg = sum(1 for r in rets if r < 0) / len(rets) * 100
        print(f"  {regime:<15} {len(rets):>5} {r_avg:>+7.2f}% {r_med:>+7.2f}% {r_neg:>7.1f}%")

    # 전략별 분석
    print(f"\n  {'전략':<25} {'건수':>5} {'평균':>8} {'중위값':>8} {'하락%':>8}")
    print("  " + "-" * 60)
    strat_groups: dict[str, list[float]] = {}
    for b in with_fwd:
        st = b["signal_type"].value if hasattr(b["signal_type"], "value") else str(b["signal_type"])
        strat_groups.setdefault(st, []).append(b["fwd_5d_pct"])

    for strat, rets in sorted(strat_groups.items(), key=lambda x: -len(x[1])):
        s_avg = statistics.mean(rets)
        s_med = statistics.median(rets)
        s_neg = sum(1 for r in rets if r < 0) / len(rets) * 100
        print(f"  {strat:<25} {len(rets):>5} {s_avg:>+7.2f}% {s_med:>+7.2f}% {s_neg:>7.1f}%")

    # 개별 케이스 (상위 10건, 이격률 높은 순)
    print("\n  이격률 높은 차단 케이스 Top 10:")
    print(f"  {'날짜':<12} {'종목':<10} {'전략':<20} {'이격률':>8} {'임계값':>7} {'5일후':>8}")
    print("  " + "-" * 70)
    sorted_blocked = sorted(with_fwd, key=lambda x: x["disparity_60d"] or 0, reverse=True)
    for b in sorted_blocked[:10]:
        st = b["signal_type"].value if hasattr(b["signal_type"], "value") else str(b["signal_type"])
        print(
            f"  {b['date']} {b['stock_name']:<8} {st:<20}"
            f" {b['disparity_60d']:>+7.1f}% {b['threshold']:>6.0f}% {b['fwd_5d_pct']:>+7.2f}%"
        )


def print_comparison(metrics_off, metrics_on, blocked_count: int) -> None:
    """ON vs OFF 비교 출력."""
    print("\n" + "=" * 70)
    print("  백테스트 비교: Filter OFF vs Filter ON")
    print("=" * 70)

    def _val(m, key):
        return getattr(m, key, None) or 0

    rows = [
        ("총 수익률 (%)", "total_return_pct", ".2f"),
        ("연환산 수익률 (%)", "annualized_return_pct", ".2f"),
        ("최대 낙폭 MDD (%)", "max_drawdown_pct", ".2f"),
        ("샤프 비율", "sharpe_ratio", ".3f"),
        ("총 매수", "total_buys", "d"),
        ("총 매도", "total_sells", "d"),
        ("승률 (%)", "win_rate_pct", ".1f"),
        ("Profit Factor", "profit_factor", ".2f"),
        ("평균 보유일", "avg_holding_days", ".1f"),
    ]

    print(f"\n  {'지표':<22} {'Filter OFF':>14} {'Filter ON':>14} {'차이':>12}")
    print("  " + "-" * 64)

    for label, key, fmt in rows:
        v_off = _val(metrics_off, key)
        v_on = _val(metrics_on, key)
        diff = v_on - v_off
        sign = "+" if diff > 0 else ""
        print(f"  {label:<22} {v_off:>14{fmt}} {v_on:>14{fmt}} {sign}{diff:>11{fmt}}")

    print(f"\n  필터로 차단된 매수: {blocked_count}건")
    if _val(metrics_off, "total_buys") > 0:
        pct = blocked_count / _val(metrics_off, "total_buys") * 100
        print(f"  시그널 감소율: {pct:.1f}%")

    # 전략별 비교
    print("\n  전략별 성과 비교:")
    print(f"  {'전략':<25} {'OFF 건수':>8} {'OFF 승률':>8} {'ON 건수':>8} {'ON 승률':>8}")
    print("  " + "-" * 62)

    strats_off = metrics_off.strategy_stats or {}
    strats_on = metrics_on.strategy_stats or {}
    all_strats = sorted(set(strats_off.keys()) | set(strats_on.keys()))

    for st in all_strats:
        s_off = strats_off.get(st)
        s_on = strats_on.get(st)
        c_off = s_off["count"] if s_off else 0
        w_off = f"{s_off['win_rate']:.1f}%" if s_off else "-"
        c_on = s_on["count"] if s_on else 0
        w_on = f"{s_on['win_rate']:.1f}%" if s_on else "-"
        print(f"  {st:<25} {c_off:>8} {w_off:>8} {c_on:>8} {w_on:>8}")


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

    print("=" * 70)
    print("  Overextension Filter 백테스트 비교 분석")
    print("=" * 70)
    print(f"  기간: {start_date} ~ {end_date}")
    print(f"  자본금: {args.capital:,} KRW")
    print()

    # 데이터 로드 (1회만)
    db_engine = get_engine()
    with Session(db_engine) as session:
        print("  데이터 로드 중...")
        price_cache = load_prices(session, start_date, end_date, buffer_days=60)
        watchlists = load_watchlists(session, start_date, end_date)
        macro_days = load_macro_days(session, start_date, end_date)

    trading_dates = get_trading_dates(price_cache, start_date, end_date)
    print(f"  거래일: {len(trading_dates)}일, 워치리스트: {len(watchlists)}일분")

    if not trading_dates:
        print("ERROR: No trading dates found")
        sys.exit(1)

    # Run 1: Filter OFF (baseline)
    print("\n  [1/2] 백테스트 실행: Filter OFF...")
    _, metrics_off, _ = run_backtest(
        start_date,
        end_date,
        args.capital,
        price_cache,
        watchlists,
        macro_days,
        trading_dates,
        overextension_filter=False,
    )

    # Run 2: Filter ON
    print("  [2/2] 백테스트 실행: Filter ON...")
    _, metrics_on, engine_on = run_backtest(
        start_date,
        end_date,
        args.capital,
        price_cache,
        watchlists,
        macro_days,
        trading_dates,
        overextension_filter=True,
    )

    # 비교 출력
    print_comparison(metrics_off, metrics_on, len(engine_on.blocked_by_overextension))

    # 차단 케이스 사후 분석
    analyze_blocked(engine_on.blocked_by_overextension)

    print("\n" + "=" * 70)
    print("  분석 완료")
    print("=" * 70)


if __name__ == "__main__":
    main()
