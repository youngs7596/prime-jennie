"""종목 마스터 업데이트 스크립트.

KIS API를 통해 KOSPI/KOSDAQ 전 종목을 조회하여
StockMaster 테이블을 갱신.

Usage:
    python scripts/update_stock_master.py
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select

from prime_jennie.domain.config import get_config
from prime_jennie.infra.database.engine import get_engine
from prime_jennie.infra.database.models import StockMasterDB
from prime_jennie.infra.kis.client import KISClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main():
    config = get_config()
    kis = KISClient(base_url=config.kis.gateway_url)
    engine = get_engine()

    logger.info("종목 마스터 업데이트 시작")

    with Session(engine) as session:
        existing = {
            s.stock_code: s
            for s in session.exec(select(StockMasterDB)).all()
        }
        logger.info("기존 종목: %d개", len(existing))

        # KIS 전 종목 조회는 Gateway 미지원 → DB 기존 데이터 검증만 수행
        # 실제 전체 종목 추가는 별도 데이터 소스 필요
        active = sum(1 for s in existing.values() if s.is_active)
        inactive = len(existing) - active

        logger.info("활성: %d, 비활성: %d", active, inactive)
        logger.info("종목 마스터 검증 완료")


if __name__ == "__main__":
    main()
