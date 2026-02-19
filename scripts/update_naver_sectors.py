"""네이버 업종 분류 업데이트 스크립트.

네이버 금융에서 업종 분류(79개 세분류)를 크롤링하여
StockMaster.sector_naver 필드를 갱신.

Usage:
    python scripts/update_naver_sectors.py
"""

import logging
import sys
from pathlib import Path

# 프로젝트 루트를 path에 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select

from prime_jennie.domain.config import get_config
from prime_jennie.infra.crawlers.naver import build_naver_sector_mapping
from prime_jennie.infra.database.engine import get_engine
from prime_jennie.infra.database.models import StockMasterDB

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main():
    logger.info("네이버 업종 분류 업데이트 시작")

    # 1. 네이버 크롤링
    mapping = build_naver_sector_mapping()
    if not mapping:
        logger.error("네이버 업종 매핑이 비어있습니다. 크롤링 실패.")
        return

    logger.info("크롤링 완료: %d개 종목 매핑", len(mapping))

    # 2. DB 업데이트
    engine = get_engine()
    updated = 0
    skipped = 0

    with Session(engine) as session:
        stocks = list(session.exec(select(StockMasterDB)).all())
        for stock in stocks:
            sector = mapping.get(stock.stock_code)
            if sector:
                if stock.sector_naver != sector:
                    stock.sector_naver = sector
                    updated += 1
            else:
                skipped += 1

        session.commit()

    logger.info(
        "업데이트 완료: %d개 갱신, %d개 미매핑 (전체 %d)",
        updated,
        skipped,
        len(stocks),
    )


if __name__ == "__main__":
    main()
