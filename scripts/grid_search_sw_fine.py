#!/usr/bin/env python3
"""SIDEWAYS 임계값 세밀 스윕 (1% 단위, 20~45%).

Phase 2에서 SIDEWAYS가 유일한 유효 국면임을 확인.
비단조적 수익률 곡선의 정확한 형태를 파악.
"""

from __future__ import annotations

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

DISABLED = 999.0


def run_single(start_date, end_date, capital, price_cache, watchlists, macro_days, trading_dates, sw_threshold):
    thresholds = {
        MarketRegime.STRONG_BULL: DISABLED,
        MarketRegime.BULL: DISABLED,
        MarketRegime.SIDEWAYS: sw_threshold,
        MarketRegime.BEAR: DISABLED,
        MarketRegime.STRONG_BEAR: DISABLED,
    }
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

    blocked = engine.blocked_by_overextension
    with_fwd = [b for b in blocked if b["fwd_5d_pct"] is not None]
    decline_count = sum(1 for b in with_fwd if b["fwd_5d_pct"] < 0) if with_fwd else 0
    decline_rate = decline_count / len(with_fwd) * 100 if with_fwd else 0.0
    avg_fwd = statistics.mean([b["fwd_5d_pct"] for b in with_fwd]) if with_fwd else 0.0

    return {
        "sw": sw_threshold,
        "total_return": metrics.total_return_pct,
        "sharpe": metrics.sharpe_ratio,
        "mdd": metrics.max_drawdown_pct,
        "win_rate": metrics.win_rate_pct,
        "total_buys": metrics.total_buys,
        "profit_factor": metrics.profit_factor,
        "blocked": len(blocked),
        "blocked_fwd": len(with_fwd),
        "decline_rate": decline_rate,
        "avg_fwd": avg_fwd,
        "avg_profit": metrics.avg_profit_pct,
        "avg_loss": metrics.avg_loss_pct,
    }


def main():
    env_path = Path(__file__).resolve().parent.parent / ".env.dev"
    load_dotenv(env_path)

    logging.basicConfig(level=logging.WARNING)

    start_date = date(2025, 12, 1)
    end_date = date(2026, 3, 7)
    capital = 50_000_000

    db_engine = get_engine()
    with Session(db_engine) as session:
        print("데이터 로드 중...")
        price_cache = load_prices(session, start_date, end_date, buffer_days=60)
        watchlists = load_watchlists(session, start_date, end_date)
        macro_days = load_macro_days(session, start_date, end_date)

    trading_dates = get_trading_dates(price_cache, start_date, end_date)
    print(f"거래일: {len(trading_dates)}일\n")

    # Baseline
    config_off = BacktestConfig(
        start_date=start_date,
        end_date=end_date,
        initial_capital=capital,
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
        initial_capital=capital,
    )
    base_ret = metrics_off.total_return_pct
    base_sharpe = metrics_off.sharpe_ratio

    print(f"Baseline: 수익률={base_ret:+.2f}%  Sharpe={base_sharpe:.3f}  매수={metrics_off.total_buys}건\n")

    # Fine sweep: SIDEWAYS 10~50 (step 1)
    print("=" * 130)
    print("  SIDEWAYS 임계값 세밀 스윕 (10% ~ 50%, 1% 단위)")
    print("=" * 130)
    print(
        f"  {'SW':>4} {'수익률':>8} {'Δ수익':>8} {'승률':>7} {'Sharpe':>7} {'MDD':>7}"
        f" {'매수':>5} {'차단':>5} {'차단(fwd)':>9} {'하락률':>7} {'차단평균':>8}"
        f" {'평균익':>7} {'평균손':>7} {'PF':>7}"
        f" {'시각화'}"
    )
    print("  " + "-" * 126)

    results = []
    for sw in range(10, 51):
        r = run_single(
            start_date,
            end_date,
            capital,
            price_cache,
            watchlists,
            macro_days,
            trading_dates,
            float(sw),
        )
        results.append(r)

        delta = r["total_return"] - base_ret
        sign = "+" if delta > 0 else ""
        dr = f"{r['decline_rate']:.0f}%" if r["blocked_fwd"] > 0 else "-"
        af = f"{r['avg_fwd']:+.1f}%" if r["blocked_fwd"] > 0 else "-"

        # ASCII bar chart for return
        bar_len = max(0, int((r["total_return"] + 5) * 2))  # scale: 2 chars per %
        bar = "█" * min(bar_len, 30)
        marker = " ◀ BEST" if r["total_return"] == max(x["total_return"] for x in results) else ""

        print(
            f"  {sw:>4}"
            f" {r['total_return']:>+7.2f}%"
            f" {sign}{delta:>7.2f}%"
            f" {r['win_rate']:>6.1f}%"
            f" {r['sharpe']:>7.3f}"
            f" {r['mdd']:>6.2f}%"
            f" {r['total_buys']:>5}"
            f" {r['blocked']:>5}"
            f" {r['blocked_fwd']:>9}"
            f" {dr:>7}"
            f" {af:>8}"
            f" {r['avg_profit']:>+6.2f}%"
            f" {r['avg_loss']:>+6.2f}%"
            f" {r['profit_factor']:>7.2f}"
            f" {bar}{marker}"
        )

    # 요약
    best_ret = max(results, key=lambda x: x["total_return"])
    best_sharpe = max(results, key=lambda x: x["sharpe"])
    # 차단 5건 이상 중 최고 하락률
    with_blocks = [r for r in results if r["blocked_fwd"] >= 5]
    best_decline = max(with_blocks, key=lambda x: x["decline_rate"]) if with_blocks else None

    print(f"\n  요약:")
    print(
        f"    최고 수익률:   SW={best_ret['sw']:.0f}% → {best_ret['total_return']:+.2f}% (Δ{best_ret['total_return'] - base_ret:+.2f}%)"
    )
    print(
        f"    최고 Sharpe:   SW={best_sharpe['sw']:.0f}% → Sharpe {best_sharpe['sharpe']:.3f} (수익률 {best_sharpe['total_return']:+.2f}%)"
    )
    if best_decline:
        print(
            f"    최고 하락률:   SW={best_decline['sw']:.0f}% → 하락률 {best_decline['decline_rate']:.0f}% ({best_decline['blocked_fwd']}건)"
        )

    # "Sweet Spot" 분석: 수익률 > baseline AND 차단 하락률 >= 55%
    print(f"\n  Sweet Spot (수익률 > baseline AND 차단 하락률 >= 55%):")
    for r in results:
        if r["total_return"] > base_ret and r["blocked_fwd"] >= 3 and r["decline_rate"] >= 55:
            print(
                f"    SW={r['sw']:.0f}%  수익률={r['total_return']:+.2f}%  Sharpe={r['sharpe']:.3f}"
                f"  차단={r['blocked']}건  하락률={r['decline_rate']:.0f}%  평균={r['avg_fwd']:+.1f}%"
            )


if __name__ == "__main__":
    main()
