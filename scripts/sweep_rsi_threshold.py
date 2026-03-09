#!/usr/bin/env python3
"""RSI_REBOUND threshold 파라미터 스윕.

각 threshold 값에 대해 백테스트를 실행하고 결과를 비교.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from unittest.mock import patch

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


@dataclass
class SweepResult:
    threshold_label: str
    total_return_pct: float
    max_drawdown_pct: float
    sharpe: float
    total_buys: int
    total_sells: int
    win_rate: float
    rsi_rebound_count: int
    rsi_rebound_win_rate: float
    rsi_rebound_avg_pnl: float
    avg_holding_days: float


def make_rsi_checker(thresholds: dict[MarketRegime, float]):
    """국면별 RSI threshold를 주입한 _check_rsi_rebound 생성."""

    from prime_jennie.services.buyer.position_sizing import calculate_rsi

    def _check_rsi_rebound(close_prices: list[float], regime: MarketRegime) -> bool:
        if len(close_prices) < 16:
            return False

        rsi_now = calculate_rsi(close_prices, period=14)
        rsi_prev = calculate_rsi(close_prices[:-1], period=14)

        if rsi_now is None or rsi_prev is None:
            return False

        threshold = thresholds.get(regime, 35.0)
        return rsi_prev <= threshold and rsi_now > threshold

    return _check_rsi_rebound


def run_sweep(
    start_date: date,
    end_date: date,
    capital: int,
    threshold_configs: list[tuple[str, dict[MarketRegime, float]]],
) -> list[SweepResult]:
    """여러 threshold 설정으로 백테스트 반복 실행."""

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")

    engine = get_engine()
    with Session(engine) as session:
        price_cache = load_prices(session, start_date, end_date, buffer_days=60)
        watchlists = load_watchlists(session, start_date, end_date)
        macro_days = load_macro_days(session, start_date, end_date)

    trading_dates = get_trading_dates(price_cache, start_date, end_date)
    print(f"Period: {start_date} ~ {end_date} ({len(trading_dates)} trading days)\n")

    results: list[SweepResult] = []

    for label, thresholds in threshold_configs:
        config = BacktestConfig(
            start_date=start_date,
            end_date=end_date,
            initial_capital=capital,
        )

        custom_checker = make_rsi_checker(thresholds)

        # daily_strategies 모듈의 _check_rsi_rebound를 교체
        with patch(
            "prime_jennie.services.backtest.daily_strategies._check_rsi_rebound",
            custom_checker,
        ):
            bt = BacktestEngine(
                config=config,
                price_cache=price_cache,
                watchlists=watchlists,
                macro_days=macro_days,
                trading_dates=trading_dates,
            )
            portfolio = bt.run()

        metrics = calculate_metrics(
            snapshots=portfolio.daily_snapshots,
            trade_logs=portfolio.trade_logs,
            initial_capital=capital,
        )

        # RSI_REBOUND 전략 통계 추출
        rsi_stats = metrics.strategy_stats.get("RSI_REBOUND", {})
        rsi_count = rsi_stats.get("count", 0)
        rsi_wr = rsi_stats.get("win_rate", 0.0)
        rsi_avg = rsi_stats.get("avg_pnl", 0.0)

        results.append(
            SweepResult(
                threshold_label=label,
                total_return_pct=metrics.total_return_pct,
                max_drawdown_pct=metrics.max_drawdown_pct,
                sharpe=metrics.sharpe_ratio,
                total_buys=metrics.total_buys,
                total_sells=metrics.total_sells,
                win_rate=metrics.win_rate_pct,
                rsi_rebound_count=rsi_count,
                rsi_rebound_win_rate=rsi_wr,
                rsi_rebound_avg_pnl=rsi_avg,
                avg_holding_days=metrics.avg_holding_days,
            )
        )

        print(f"  [{label}] return={metrics.total_return_pct:+.2f}%, "
              f"MDD={metrics.max_drawdown_pct:.2f}%, "
              f"sharpe={metrics.sharpe_ratio:.2f}, "
              f"RSI_REB={rsi_count}건 "
              f"(WR={rsi_wr:.1f}%, avg={rsi_avg:+.2f}%)")

    return results


def main() -> None:
    logging.basicConfig(level=logging.WARNING)

    # threshold 시나리오 정의
    # 운영: BULL 비활성화, SIDEWAYS=40, BEAR=30, STRONG_BEAR=25
    # 백테스트 기존: STRONG_BULL=30, BULL=35, SIDEWAYS=35, BEAR=40, STRONG_BEAR=40
    scenarios = [
        (
            "PROD (SW=40,B=30)",  # 현재 운영 설정 (BULL 비활성 = threshold 0으로 비활)
            {
                MarketRegime.STRONG_BULL: 0.0,  # 사실상 비활성
                MarketRegime.BULL: 0.0,          # 사실상 비활성
                MarketRegime.SIDEWAYS: 40.0,
                MarketRegime.BEAR: 30.0,
                MarketRegime.STRONG_BEAR: 25.0,
            },
        ),
        (
            "BT기존 (SW=35,B=40)",  # 현재 백테스트 설정
            {
                MarketRegime.STRONG_BULL: 30.0,
                MarketRegime.BULL: 35.0,
                MarketRegime.SIDEWAYS: 35.0,
                MarketRegime.BEAR: 40.0,
                MarketRegime.STRONG_BEAR: 40.0,
            },
        ),
        (
            "보수적 (SW=30,B=25)",
            {
                MarketRegime.STRONG_BULL: 0.0,
                MarketRegime.BULL: 0.0,
                MarketRegime.SIDEWAYS: 30.0,
                MarketRegime.BEAR: 25.0,
                MarketRegime.STRONG_BEAR: 20.0,
            },
        ),
        (
            "중간 (SW=35,B=30)",
            {
                MarketRegime.STRONG_BULL: 0.0,
                MarketRegime.BULL: 0.0,
                MarketRegime.SIDEWAYS: 35.0,
                MarketRegime.BEAR: 30.0,
                MarketRegime.STRONG_BEAR: 25.0,
            },
        ),
        (
            "공격적 (SW=45,B=35)",
            {
                MarketRegime.STRONG_BULL: 0.0,
                MarketRegime.BULL: 0.0,
                MarketRegime.SIDEWAYS: 45.0,
                MarketRegime.BEAR: 35.0,
                MarketRegime.STRONG_BEAR: 30.0,
            },
        ),
        (
            "비활성 (전부OFF)",
            {
                MarketRegime.STRONG_BULL: 0.0,
                MarketRegime.BULL: 0.0,
                MarketRegime.SIDEWAYS: 0.0,
                MarketRegime.BEAR: 0.0,
                MarketRegime.STRONG_BEAR: 0.0,
            },
        ),
    ]

    # --- Period 1: 2025 H1 (워치리스트 있는 구간) ---
    print("=" * 70)
    print("  Period 1: 2025-01-01 ~ 2025-06-30")
    print("=" * 70)
    results_h1 = run_sweep(
        date(2025, 1, 1), date(2025, 6, 30), 50_000_000, scenarios
    )

    # --- Period 2: 2026 최신 (매크로 데이터 + 확장 워치리스트) ---
    print()
    print("=" * 70)
    print("  Period 2: 2026-01-01 ~ 2026-03-07")
    print("=" * 70)
    results_26 = run_sweep(
        date(2026, 1, 1), date(2026, 3, 7), 50_000_000, scenarios
    )

    # --- 종합 비교 테이블 ---
    print()
    print("=" * 70)
    print("  종합 비교")
    print("=" * 70)

    header = (
        f"{'시나리오':<22} {'수익률':>8} {'MDD':>8} {'Sharpe':>7}"
        f" {'전체WR':>7} {'RSI건수':>7} {'RSI_WR':>7} {'RSI평균':>8}"
    )
    print("\n[2025 H1]")
    print(header)
    print("-" * 85)
    for r in results_h1:
        print(
            f"{r.threshold_label:<22} {r.total_return_pct:>+7.2f}% "
            f"{r.max_drawdown_pct:>7.2f}% {r.sharpe:>7.2f} "
            f"{r.win_rate:>6.1f}% {r.rsi_rebound_count:>6}건 "
            f"{r.rsi_rebound_win_rate:>6.1f}% {r.rsi_rebound_avg_pnl:>+7.2f}%"
        )

    print("\n[2026 Jan-Mar]")
    print(header)
    print("-" * 85)
    for r in results_26:
        print(
            f"{r.threshold_label:<22} {r.total_return_pct:>+7.2f}% "
            f"{r.max_drawdown_pct:>7.2f}% {r.sharpe:>7.2f} "
            f"{r.win_rate:>6.1f}% {r.rsi_rebound_count:>6}건 "
            f"{r.rsi_rebound_win_rate:>6.1f}% {r.rsi_rebound_avg_pnl:>+7.2f}%"
        )


if __name__ == "__main__":
    main()
