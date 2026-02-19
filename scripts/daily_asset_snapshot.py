"""일일 자산 스냅샷 스크립트.

현재 계좌 잔고를 조회하여 DailyAssetSnapshot 테이블에 저장.
Airflow DAG에서 매일 15:45 KST에 실행.

Usage:
    python scripts/daily_asset_snapshot.py
"""

import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session

from prime_jennie.domain.config import get_config
from prime_jennie.infra.database.engine import get_engine
from prime_jennie.infra.database.models import DailyAssetSnapshotDB
from prime_jennie.infra.kis.client import KISClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main():
    config = get_config()
    kis = KISClient(base_url=config.kis.gateway_url)
    engine = get_engine()

    logger.info("일일 자산 스냅샷 수집 시작")

    try:
        # 계좌 잔고 조회
        positions = kis.get_positions()
        cash = kis.get_cash_balance()

        stock_eval = sum(p.quantity * (p.current_price or p.average_buy_price) for p in positions)
        total_asset = cash + stock_eval

        snapshot = DailyAssetSnapshotDB(
            snapshot_date=date.today(),
            total_asset=total_asset,
            cash_balance=cash,
            stock_eval_amount=stock_eval,
            position_count=len(positions),
        )

        with Session(engine) as session:
            session.add(snapshot)
            session.commit()

        logger.info(
            "스냅샷 저장: 총자산=%s, 현금=%s, 주식=%s, 종목=%d",
            f"{total_asset:,}",
            f"{cash:,}",
            f"{stock_eval:,}",
            len(positions),
        )

    except Exception as e:
        logger.error("스냅샷 수집 실패: %s", e)
        raise


if __name__ == "__main__":
    main()
