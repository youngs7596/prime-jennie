#!/usr/bin/env python3
"""KIS 계좌 ↔ DB 포지션 동기화 CLI.

Usage:
    uv run python scripts/sync_positions.py              # dry-run (기본)
    uv run python scripts/sync_positions.py --apply      # 실제 적용
    uv run python scripts/sync_positions.py --apply --auto-confirm
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlmodel import Session, select

from prime_jennie.domain.config import get_config
from prime_jennie.infra.database.engine import get_engine
from prime_jennie.infra.database.models import PositionDB
from prime_jennie.infra.kis.client import KISClient
from prime_jennie.services.jobs.app import apply_sync, compare_positions


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="KIS 계좌 ↔ DB 포지션 동기화")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="비교 결과만 출력 (기본값)",
    )
    group.add_argument(
        "--apply",
        action="store_true",
        help="실제 DB 반영",
    )
    parser.add_argument(
        "--auto-confirm",
        action="store_true",
        help="확인 프롬프트 없이 적용 (--apply와 함께 사용)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="상세 로깅",
    )
    return parser.parse_args()


def print_report(diff: dict, kis_count: int, db_count: int) -> None:
    """비교 결과를 콘솔에 출력."""
    print(f"\n{'=' * 60}")
    print("KIS 계좌 ↔ DB 포지션 비교 결과")
    print(f"{'=' * 60}")
    print(f"  KIS 보유: {kis_count}종목")
    print(f"  DB 포지션: {db_count}종목")
    print(f"  일치: {len(diff['matched'])}종목")
    print()

    if diff["only_in_kis"]:
        print(f"[+] KIS에만 있음 ({len(diff['only_in_kis'])}종목) → DB INSERT 필요")
        for p in diff["only_in_kis"]:
            print(
                f"    {p['stock_code']} {p.get('stock_name', ''):<12s} "
                f"qty={p.get('quantity', 0):>6,} avg={p.get('average_buy_price', 0):>10,}"
            )
        print()

    if diff["only_in_db"]:
        print(f"[-] DB에만 있음 ({len(diff['only_in_db'])}종목) → DB DELETE 필요")
        for p in diff["only_in_db"]:
            print(f"    {p.stock_code} {p.stock_name:<12s} qty={p.quantity:>6,} avg={p.average_buy_price:>10,}")
        print()

    if diff["quantity_mismatch"]:
        print(f"[~] 수량 불일치 ({len(diff['quantity_mismatch'])}종목) → DB UPDATE 필요")
        for m in diff["quantity_mismatch"]:
            print(f"    {m['stock_code']} {m['stock_name']:<12s} qty: {m['db_qty']:>6,} → {m['kis_qty']:>6,}")
        print()

    if diff["price_mismatch"]:
        print(f"[~] 평단가 불일치 ({len(diff['price_mismatch'])}종목) → DB UPDATE 필요")
        for m in diff["price_mismatch"]:
            print(f"    {m['stock_code']} {m['stock_name']:<12s} avg: {m['db_avg']:>10,} → {m['kis_avg']:>10,}")
        print()

    has_diff = diff["only_in_kis"] or diff["only_in_db"] or diff["quantity_mismatch"] or diff["price_mismatch"]
    if not has_diff:
        print("모든 포지션 일치 — 동기화 불필요")

    print(f"{'=' * 60}")


def main() -> None:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    args = parse_args()

    log_level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    config = get_config()
    kis = KISClient(base_url=config.kis.gateway_url)

    print("KIS 잔고 조회 중...")
    try:
        balance = kis.get_balance()
    except Exception as e:
        print(f"ERROR: KIS Gateway 연결 실패 — {e}")
        sys.exit(1)

    kis_positions = balance.get("positions", [])

    engine = get_engine()
    with Session(engine) as session:
        db_positions = list(session.exec(select(PositionDB)).all())
        diff = compare_positions(kis_positions, db_positions)
        print_report(diff, len(kis_positions), len(db_positions))

        has_diff = diff["only_in_kis"] or diff["only_in_db"] or diff["quantity_mismatch"] or diff["price_mismatch"]

        if not has_diff:
            sys.exit(0)

        if not args.apply:
            print("\n[DRY RUN] --apply 옵션으로 실제 적용 가능")
            sys.exit(0)

        if not args.auto_confirm:
            answer = input("\n위 변경사항을 DB에 적용하시겠습니까? (y/N): ")
            if answer.strip().lower() != "y":
                print("취소됨")
                sys.exit(0)

        actions = apply_sync(session, diff, kis_positions)
        session.commit()

        print(f"\n{len(actions)}건 적용 완료:")
        for a in actions:
            print(f"  {a}")


if __name__ == "__main__":
    main()
