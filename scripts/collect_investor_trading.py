"""투자자별 매매동향 수집 스크립트.

pykrx를 통해 외인/기관/개인 순매수 데이터를 수집하여 DB에 저장.
Airflow DAG에서 매일 18:30 KST에 실행.

Usage:
    python scripts/collect_investor_trading.py [--days 5]
"""

import argparse
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session

from prime_jennie.domain.config import get_config
from prime_jennie.infra.database.engine import get_engine
from prime_jennie.infra.database.models import StockInvestorTradingDB

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=1, help="수집할 일수")
    args = parser.parse_args()

    logger.info("투자자별 매매동향 수집 시작 (days=%d)", args.days)

    try:
        from pykrx import stock as pykrx_stock
    except ImportError:
        logger.error("pykrx가 설치되어 있지 않습니다: pip install pykrx")
        return

    engine = get_engine()
    today = date.today()
    start_date = today - timedelta(days=args.days + 5)  # 영업일 보정

    # KOSPI + KOSDAQ 주요 종목
    with Session(engine) as session:
        from sqlmodel import select
        from prime_jennie.infra.database.models import StockMasterDB

        stocks = list(
            session.exec(
                select(StockMasterDB).where(StockMasterDB.is_active == True)
            ).all()
        )

    logger.info("대상 종목: %d개", len(stocks))
    saved = 0

    for stock in stocks:
        try:
            df = pykrx_stock.get_market_trading_value_by_date(
                start_date.strftime("%Y%m%d"),
                today.strftime("%Y%m%d"),
                stock.stock_code,
            )
            if df.empty:
                continue

            with Session(engine) as session:
                for idx, row in df.iterrows():
                    trade_date = idx.date() if hasattr(idx, "date") else idx
                    record = StockInvestorTradingDB(
                        stock_code=stock.stock_code,
                        trade_date=trade_date,
                        foreign_net=int(row.get("외국인합계", 0)),
                        institution_net=int(row.get("기관합계", 0)),
                        retail_net=int(row.get("개인", 0)),
                    )
                    session.merge(record)
                session.commit()
                saved += 1

        except Exception as e:
            logger.debug("[%s] 수급 수집 실패: %s", stock.stock_code, e)

    logger.info("수집 완료: %d/%d 종목", saved, len(stocks))


if __name__ == "__main__":
    main()
