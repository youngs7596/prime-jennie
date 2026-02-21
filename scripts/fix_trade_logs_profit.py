#!/usr/bin/env python3
"""기존 trade_logs의 NULL profit_pct / profit_amount / holding_days 복구.

매도 로그마다 가장 최근 매수 로그를 매칭하여 수익률과 보유일 계산.
부분 매도(scale-out)의 경우 동일 종목 최초 매수가를 사용.

Usage:
    uv run python scripts/fix_trade_logs_profit.py          # dry-run (기본)
    uv run python scripts/fix_trade_logs_profit.py --apply   # 실제 적용
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from sqlmodel import Session, text  # noqa: E402

from prime_jennie.infra.database.engine import get_engine  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="trade_logs profit_pct 복구")
    parser.add_argument("--apply", action="store_true", help="실제 DB 업데이트 (기본: dry-run)")
    args = parser.parse_args()

    engine = get_engine()

    with Session(engine) as session:
        # NULL profit_pct인 SELL 로그 조회
        sells = session.exec(
            text("""
            SELECT id, stock_code, stock_name, price, quantity, trade_timestamp, reason
            FROM trade_logs
            WHERE trade_type = 'SELL' AND profit_pct IS NULL
            ORDER BY trade_timestamp
        """)
        ).all()

        logger.info("Found %d SELL logs with NULL profit_pct", len(sells))

        updated = 0
        skipped = 0

        for sell in sells:
            sell_id = sell[0]
            stock_code = sell[1]
            stock_name = sell[2]
            sell_price = sell[3]
            sell_qty = sell[4]
            sell_ts = sell[5]

            # 해당 종목의 매도 이전 가장 최근 BUY 매칭
            # 부분 매도 고려: 같은 종목의 첫 매수를 기준으로 (평균 매수가 역할)
            buy = session.exec(
                text(
                    "SELECT id, price, quantity, trade_timestamp, strategy_signal "
                    "FROM trade_logs "
                    "WHERE trade_type = 'BUY' "
                    "  AND stock_code = :code "
                    "  AND trade_timestamp < :ts "
                    "ORDER BY trade_timestamp DESC LIMIT 1"
                ).bindparams(code=stock_code, ts=sell_ts)
            ).first()

            if not buy:
                logger.debug("SELL #%d %s: no matching BUY", sell_id, stock_name)
                skipped += 1
                continue

            buy_price = buy[1]
            buy_ts = buy[3]
            buy_signal = buy[4]

            if buy_price <= 0:
                logger.warning("SELL #%d %s: buy_price=0", sell_id, stock_name)
                skipped += 1
                continue

            # 계산
            profit_pct = round((sell_price - buy_price) / buy_price * 100, 2)
            profit_amount = (sell_price - buy_price) * sell_qty
            holding_days = (sell_ts - buy_ts).days

            logger.info(
                "SELL #%4d %-10s sell=%8s buy=%8s pnl=%+7.2f%% days=%3d signal=%s",
                sell_id,
                stock_name,
                f"{sell_price:,}",
                f"{buy_price:,}",
                profit_pct,
                holding_days,
                buy_signal,
            )

            if args.apply:
                session.exec(
                    text(
                        "UPDATE trade_logs "
                        "SET profit_pct = :pnl, "
                        "    profit_amount = :amt, "
                        "    holding_days = :days "
                        "WHERE id = :id"
                    ).bindparams(
                        pnl=profit_pct,
                        amt=profit_amount,
                        days=holding_days,
                        id=sell_id,
                    )
                )
                updated += 1

        if args.apply:
            session.commit()
            logger.info("Updated %d rows, skipped %d", updated, skipped)
        else:
            logger.info("[DRY-RUN] Would update %d rows, skip %d", len(sells) - skipped, skipped)
            logger.info("Run with --apply to execute")


if __name__ == "__main__":
    main()
