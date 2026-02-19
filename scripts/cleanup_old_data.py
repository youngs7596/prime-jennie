"""오래된 데이터 정리 스크립트.

90일 이상 지난 분봉, 뉴스 감성, 투자자 매매동향 데이터를 삭제.
Airflow DAG에서 매주 일요일 03:00 KST에 실행.

Usage:
    python scripts/cleanup_old_data.py [--days 90]
"""

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, delete

from prime_jennie.infra.database.engine import get_engine
from prime_jennie.infra.database.models import (
    StockInvestorTradingDB,
    StockNewsSentimentDB,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=90, help="보존 기간 (일)")
    args = parser.parse_args()

    cutoff = date.today() - timedelta(days=args.days)
    engine = get_engine()

    logger.info("데이터 정리 시작 (기준일: %s)", cutoff)

    with Session(engine) as session:
        # 뉴스 감성
        result = session.exec(
            delete(StockNewsSentimentDB).where(
                StockNewsSentimentDB.news_date < cutoff
            )
        )
        news_deleted = result.rowcount if hasattr(result, "rowcount") else 0

        # 투자자 매매동향
        result = session.exec(
            delete(StockInvestorTradingDB).where(
                StockInvestorTradingDB.trade_date < cutoff
            )
        )
        trading_deleted = result.rowcount if hasattr(result, "rowcount") else 0

        session.commit()

    logger.info(
        "정리 완료: 뉴스 %d건, 수급 %d건 삭제",
        news_deleted,
        trading_deleted,
    )


if __name__ == "__main__":
    main()
