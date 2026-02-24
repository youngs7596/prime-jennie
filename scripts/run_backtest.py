#!/usr/bin/env python3
"""백테스트 CLI 엔트리포인트.

Usage:
    uv run python scripts/run_backtest.py --start 2025-12-01 --end 2026-02-21
    uv run python scripts/run_backtest.py --start 2025-12-01 --end 2026-02-21 \
        --capital 100000000 --export-csv ./results/
"""

from __future__ import annotations

import argparse
import logging
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
from prime_jennie.services.backtest.metrics import (
    calculate_metrics,
    export_csv,
    print_report,
)
from prime_jennie.services.backtest.models import BacktestConfig


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="prime-jennie E2E 백테스트")
    parser.add_argument(
        "--start",
        required=True,
        help="시작일 (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end",
        required=True,
        help="종료일 (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--capital",
        type=int,
        default=50_000_000,
        help="초기 투자금 (기본: 50,000,000원)",
    )
    parser.add_argument(
        "--slippage",
        type=float,
        default=0.1,
        help="슬리피지 %% (기본: 0.1)",
    )
    parser.add_argument(
        "--export-csv",
        type=str,
        default=None,
        help="CSV 내보내기 디렉토리",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="상세 로깅",
    )
    return parser.parse_args()


def main() -> None:
    # .env 로드 (로컬 실행 시 DB 비밀번호 등)
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")

    args = parse_args()

    # 로깅
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    start_date = date.fromisoformat(args.start)
    end_date = date.fromisoformat(args.end)

    if start_date >= end_date:
        print("ERROR: start date must be before end date")
        sys.exit(1)

    config = BacktestConfig(
        start_date=start_date,
        end_date=end_date,
        initial_capital=args.capital,
        slippage_pct=args.slippage,
        export_csv_dir=args.export_csv,
    )

    print(f"\nBacktest: {start_date} ~ {end_date}")
    print(f"Capital: {config.initial_capital:,} KRW")
    print(f"Slippage: {config.slippage_pct}%\n")

    # DB 연결 + 데이터 로드
    engine = get_engine()
    with Session(engine) as session:
        print("Loading price data...")
        price_cache = load_prices(session, start_date, end_date, buffer_days=60)

        print("Loading watchlists...")
        watchlists = load_watchlists(session, start_date, end_date)

        print("Loading macro insights...")
        macro_days = load_macro_days(session, start_date, end_date)

    trading_dates = get_trading_dates(price_cache, start_date, end_date)
    print(f"Trading dates: {len(trading_dates)}")

    if not trading_dates:
        print("ERROR: No trading dates found in range")
        sys.exit(1)

    # 엔진 실행
    bt = BacktestEngine(
        config=config,
        price_cache=price_cache,
        watchlists=watchlists,
        macro_days=macro_days,
        trading_dates=trading_dates,
    )

    portfolio = bt.run()

    # 성과 분석
    metrics = calculate_metrics(
        snapshots=portfolio.daily_snapshots,
        trade_logs=portfolio.trade_logs,
        initial_capital=config.initial_capital,
    )

    print_report(metrics)

    # CSV 내보내기
    if config.export_csv_dir:
        export_csv(
            trade_logs=portfolio.trade_logs,
            snapshots=portfolio.daily_snapshots,
            output_dir=config.export_csv_dir,
        )


if __name__ == "__main__":
    main()
