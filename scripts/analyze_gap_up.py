#!/usr/bin/env python3
"""GAP_UP_REBOUND 전략 역사적 성과 분석.

두 가지 버전 비교:
  A) Production (완화): +2% 갭업 + 1.5x 거래량 + 양봉 (시가 유지)
  B) Backtest  (엄격): 전일 -3% 폭락 후 + A 조건 전부

분석 항목:
  - 시그널 발생 빈도
  - Forward returns (1d, 3d, 5d, 10d, 20d)
  - 승률, 평균 수익률
  - 국면별 성과 차이

Usage:
    uv run python scripts/analyze_gap_up.py
    uv run python scripts/analyze_gap_up.py --start 2025-06-01 --end 2026-03-12
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pymysql

# .env 로드
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass


@dataclass
class Signal:
    stock_code: str
    stock_name: str
    signal_date: date
    open_price: int
    close_price: int
    prev_close: int
    gap_pct: float
    prev_return_pct: float
    volume_ratio: float
    version: str  # "production" or "backtest"


@dataclass
class ForwardReturn:
    fwd_1d: float | None = None
    fwd_3d: float | None = None
    fwd_5d: float | None = None
    fwd_10d: float | None = None
    fwd_20d: float | None = None


def connect_db() -> pymysql.Connection:
    import os

    return pymysql.connect(
        host=os.getenv("DB_HOST", "192.168.31.195"),
        port=int(os.getenv("DB_PORT", "3307")),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", "q1w2e3R$"),
        database=os.getenv("DB_NAME", "jennie_db"),
        cursorclass=pymysql.cursors.DictCursor,
    )


def load_stock_names(conn: pymysql.Connection) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute("SELECT stock_code, stock_name FROM stock_masters")
        return {r["stock_code"]: r["stock_name"] for r in cur.fetchall()}


def load_kospi_stocks(conn: pymysql.Connection, min_market_cap: int = 500) -> set[str]:
    """KOSPI 시총 N억 이상 종목 코드 반환."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT stock_code FROM stock_masters WHERE market = 'KOSPI' AND market_cap >= %s AND is_active = 1",
            (min_market_cap * 100,),  # DB는 백만원 단위, 500억 = 50000백만
        )
        return {r["stock_code"] for r in cur.fetchall()}


def load_price_data(
    conn: pymysql.Connection,
    start_date: date,
    end_date: date,
) -> dict[str, list[dict]]:
    """종목별 일봉 데이터 로드 (날짜 오름차순)."""
    # 20일 이동평균 계산을 위해 start_date 30일 전부터 로드
    buffer_start = start_date - timedelta(days=45)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT stock_code, price_date, open_price, high_price,
                   low_price, close_price, volume
            FROM stock_daily_prices
            WHERE price_date >= %s AND price_date <= %s
            ORDER BY stock_code, price_date
            """,
            (buffer_start, end_date),
        )
        rows = cur.fetchall()

    by_stock: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_stock[r["stock_code"]].append(r)
    return dict(by_stock)


def scan_gap_up_signals(
    price_data: dict[str, list[dict]],
    stock_names: dict[str, str],
    start_date: date,
    min_gap_pct: float = 2.0,
    max_gap_pct: float = 0.0,
    min_crash_pct: float = -3.0,
    min_volume_ratio: float = 1.5,
) -> list[Signal]:
    """모든 종목에서 GAP_UP_REBOUND 시그널 탐지."""
    signals: list[Signal] = []

    for stock_code, prices in price_data.items():
        if len(prices) < 22:
            continue

        for i in range(22, len(prices)):
            today = prices[i]
            yesterday = prices[i - 1]
            day_before = prices[i - 2]

            # 분석 기간 필터
            if today["price_date"] < start_date:
                continue

            # 기본 가격 유효성
            if yesterday["close_price"] <= 0 or day_before["close_price"] <= 0:
                continue
            if today["open_price"] <= 0 or today["close_price"] <= 0:
                continue

            # 전일 등락률
            prev_return = (yesterday["close_price"] - day_before["close_price"]) / day_before["close_price"] * 100

            # 갭업 비율
            gap_pct = (today["open_price"] - yesterday["close_price"]) / yesterday["close_price"] * 100
            if gap_pct < min_gap_pct:
                continue
            if max_gap_pct > 0 and gap_pct > max_gap_pct:
                continue

            # 거래량 비율 (20일 평균 대비)
            vol_window = prices[max(0, i - 20) : i]
            if len(vol_window) < 5:
                continue
            avg_vol = sum(p["volume"] for p in vol_window) / len(vol_window)
            if avg_vol <= 0:
                continue
            vol_ratio = today["volume"] / avg_vol
            if vol_ratio < min_volume_ratio:
                continue

            # 양봉 확인 (종가 >= 시가)
            if today["close_price"] < today["open_price"]:
                continue

            # Production 버전: 갭업 + 거래량 + 양봉 (폭락 조건 없음)
            signals.append(
                Signal(
                    stock_code=stock_code,
                    stock_name=stock_names.get(stock_code, stock_code),
                    signal_date=today["price_date"],
                    open_price=today["open_price"],
                    close_price=today["close_price"],
                    prev_close=yesterday["close_price"],
                    gap_pct=gap_pct,
                    prev_return_pct=prev_return,
                    volume_ratio=vol_ratio,
                    version="production" if prev_return > min_crash_pct else "backtest",
                )
            )

    return signals


def calculate_forward_returns(
    signals: list[Signal],
    price_data: dict[str, list[dict]],
) -> list[tuple[Signal, ForwardReturn]]:
    """각 시그널의 forward return 계산."""
    # 종목별 날짜 → 인덱스 맵
    date_idx: dict[str, dict[date, int]] = {}
    for code, prices in price_data.items():
        date_idx[code] = {p["price_date"]: i for i, p in enumerate(prices)}

    results = []
    for sig in signals:
        idx_map = date_idx.get(sig.stock_code)
        if not idx_map:
            continue
        base_idx = idx_map.get(sig.signal_date)
        if base_idx is None:
            continue

        prices = price_data[sig.stock_code]
        base_price = sig.close_price
        fwd = ForwardReturn()

        for days, attr in [(1, "fwd_1d"), (3, "fwd_3d"), (5, "fwd_5d"), (10, "fwd_10d"), (20, "fwd_20d")]:
            target_idx = base_idx + days
            if target_idx < len(prices):
                future_price = prices[target_idx]["close_price"]
                if future_price > 0 and base_price > 0:
                    setattr(fwd, attr, (future_price - base_price) / base_price * 100)

        results.append((sig, fwd))

    return results


def print_report(results: list[tuple[Signal, ForwardReturn]], version: str) -> None:
    """성과 리포트 출력."""
    filtered = [(s, f) for s, f in results if s.version == version or version == "all"]
    if not filtered:
        print(f"\n  [{version}] 시그널 없음\n")
        return

    total = len(filtered)
    print(f"\n{'=' * 70}")
    print(f"  GAP_UP_REBOUND — {version.upper()} 버전")
    print(f"{'=' * 70}")
    print(f"  총 시그널: {total}건")

    # 날짜 범위
    dates = [s.signal_date for s, _ in filtered]
    print(f"  기간: {min(dates)} ~ {max(dates)}")
    print(f"  평균 갭업: {sum(s.gap_pct for s, _ in filtered) / total:.1f}%")
    print(f"  평균 거래량비: {sum(s.volume_ratio for s, _ in filtered) / total:.1f}x")

    # Forward returns
    for attr, label in [
        ("fwd_1d", "1일"),
        ("fwd_3d", "3일"),
        ("fwd_5d", "5일"),
        ("fwd_10d", "10일"),
        ("fwd_20d", "20일"),
    ]:
        vals = [getattr(f, attr) for _, f in filtered if getattr(f, attr) is not None]
        if not vals:
            continue
        wins = sum(1 for v in vals if v > 0)
        avg = sum(vals) / len(vals)
        med = sorted(vals)[len(vals) // 2]
        print(
            f"\n  {label} Forward Return (n={len(vals)}):"
            f"\n    승률: {wins}/{len(vals)} ({wins / len(vals) * 100:.1f}%)"
            f"\n    평균: {avg:+.2f}%  |  중위: {med:+.2f}%"
            f"\n    최대: {max(vals):+.2f}%  |  최소: {min(vals):+.2f}%"
        )

    # 상위 5 / 하위 5 (5일 기준)
    with_5d = [(s, f) for s, f in filtered if f.fwd_5d is not None]
    if with_5d:
        sorted_5d = sorted(with_5d, key=lambda x: x[1].fwd_5d or 0, reverse=True)
        print(f"\n  {'─' * 50}")
        print("  상위 5 (5일 수익률 기준):")
        for s, f in sorted_5d[:5]:
            print(
                f"    {s.signal_date} {s.stock_name:15s} 갭:{s.gap_pct:+.1f}% "
                f"전일:{s.prev_return_pct:+.1f}% → 5d:{f.fwd_5d:+.2f}%"
            )
        print("  하위 5:")
        for s, f in sorted_5d[-5:]:
            print(
                f"    {s.signal_date} {s.stock_name:15s} 갭:{s.gap_pct:+.1f}% "
                f"전일:{s.prev_return_pct:+.1f}% → 5d:{f.fwd_5d:+.2f}%"
            )

    print()


def print_yearly_breakdown(results: list[tuple[Signal, ForwardReturn]], version: str) -> None:
    """연도별 성과 분석."""
    filtered = [(s, f) for s, f in results if s.version == version or version == "all"]
    if not filtered:
        return

    by_year: dict[int, list[tuple[Signal, ForwardReturn]]] = defaultdict(list)
    for s, f in filtered:
        by_year[s.signal_date.year].append((s, f))

    print(f"\n  연도별 성과 ({version.upper()}):")
    print(f"  {'연도':>6s} {'건수':>5s} {'5d승률':>7s} {'5d평균':>8s} {'5d중위':>8s}")
    print(f"  {'─' * 40}")

    for year in sorted(by_year.keys()):
        items = by_year[year]
        vals_5d = [f.fwd_5d for _, f in items if f.fwd_5d is not None]
        if not vals_5d:
            continue
        wins = sum(1 for v in vals_5d if v > 0)
        avg = sum(vals_5d) / len(vals_5d)
        med = sorted(vals_5d)[len(vals_5d) // 2]
        print(f"  {year:>6d} {len(items):>5d} {wins / len(vals_5d) * 100:>6.1f}% {avg:>+7.2f}% {med:>+7.2f}%")


def main() -> None:
    parser = argparse.ArgumentParser(description="GAP_UP_REBOUND 전략 역사적 성과 분석")
    parser.add_argument("--start", default="2025-01-01", help="분석 시작일 (기본: 2025-01-01)")
    parser.add_argument("--end", default="2026-03-12", help="분석 종료일 (기본: 2026-03-12)")
    parser.add_argument("--gap", type=float, default=2.0, help="최소 갭업 %% (기본: 2.0)")
    parser.add_argument("--crash", type=float, default=-3.0, help="전일 최소 하락 %% (기본: -3.0)")
    parser.add_argument("--vol", type=float, default=1.5, help="최소 거래량 비율 (기본: 1.5)")
    parser.add_argument("--max-gap", type=float, default=0.0, help="최대 갭업 %% (기본: 0=제한없음)")
    parser.add_argument("--kospi-only", action="store_true", default=True, help="KOSPI 500억+ 종목만 (기본: True)")
    parser.add_argument("--all-stocks", action="store_true", help="전 종목 분석")
    args = parser.parse_args()

    start_date = date.fromisoformat(args.start)
    end_date = date.fromisoformat(args.end)

    print(f"\nGAP_UP_REBOUND 전략 분석")
    print(f"기간: {start_date} ~ {end_date}")
    max_gap_str = f", 갭업 <= {args.max_gap}%" if args.max_gap > 0 else ""
    print(f"조건: 갭업 >= {args.gap}%{max_gap_str}, 거래량 >= {args.vol}x, 전일하락 <= {args.crash}% (backtest)")

    use_kospi_filter = args.kospi_only and not args.all_stocks

    conn = connect_db()
    try:
        print("종목명 로딩...")
        stock_names = load_stock_names(conn)

        kospi_codes: set[str] | None = None
        if use_kospi_filter:
            kospi_codes = load_kospi_stocks(conn)
            print(f"KOSPI 500억+ 필터: {len(kospi_codes)} 종목")

        print("가격 데이터 로딩...")
        price_data = load_price_data(conn, start_date, end_date)
        if kospi_codes:
            price_data = {k: v for k, v in price_data.items() if k in kospi_codes}
        print(f"  {len(price_data)} 종목 로드 완료")

        print("시그널 탐지 중...")
        signals = scan_gap_up_signals(
            price_data,
            stock_names,
            start_date,
            min_gap_pct=args.gap,
            max_gap_pct=args.max_gap,
            min_crash_pct=args.crash,
            min_volume_ratio=args.vol,
        )
        print(f"  총 {len(signals)}건 발견")

        # Forward return 계산
        results = calculate_forward_returns(signals, price_data)

        # Production 버전 (전일 -3% 조건 없음 → production + backtest 합산)
        print_report(results, "all")
        print_yearly_breakdown(results, "all")

        # Backtest 버전만 (전일 -3% 폭락 후)
        backtest_only = [(s, f) for s, f in results if s.version == "backtest"]
        if backtest_only:
            print_report(results, "backtest")
            print_yearly_breakdown(results, "backtest")

        # Production only (전일 -3% 없이 갭업만)
        prod_only = [(s, f) for s, f in results if s.version == "production"]
        if prod_only:
            print_report(results, "production")
            print_yearly_breakdown(results, "production")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
