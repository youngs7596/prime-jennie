"""외국인 지분율 수집 스크립트.

pykrx를 통해 외국인 보유 비율 데이터를 수집.
Airflow DAG에서 매일 18:35 KST에 실행.

Usage:
    python scripts/collect_foreign_holding.py
"""

import logging
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select

from prime_jennie.domain.config import get_config
from prime_jennie.infra.database.engine import get_engine
from prime_jennie.infra.database.models import StockMasterDB

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main():
    logger.info("외국인 지분율 수집 시작")

    try:
        from pykrx import stock as pykrx_stock
    except ImportError:
        logger.error("pykrx가 설치되어 있지 않습니다: pip install pykrx")
        return

    engine = get_engine()
    today = date.today()
    date_str = today.strftime("%Y%m%d")

    with Session(engine) as session:
        stocks = list(
            session.exec(
                select(StockMasterDB).where(StockMasterDB.is_active == True)
            ).all()
        )

    logger.info("대상 종목: %d개", len(stocks))
    updated = 0

    for stock in stocks:
        try:
            df = pykrx_stock.get_exhaustion_rates_of_foreign_investment_by_date(
                (today - timedelta(days=3)).strftime("%Y%m%d"),
                date_str,
                stock.stock_code,
            )
            if df.empty:
                continue

            latest = df.iloc[-1]
            ratio = float(latest.get("지분율", 0))

            with Session(engine) as session:
                db_stock = session.get(StockMasterDB, stock.stock_code)
                if db_stock and hasattr(db_stock, "foreign_holding_ratio"):
                    db_stock.foreign_holding_ratio = ratio
                    session.commit()
                    updated += 1

        except Exception as e:
            logger.debug("[%s] 외국인 지분율 수집 실패: %s", stock.stock_code, e)

    logger.info("수집 완료: %d/%d 종목", updated, len(stocks))


if __name__ == "__main__":
    main()
