"""Stock Master 초기 시딩 스크립트.

네이버 금융 시가총액 순위 페이지에서 전 종목의 코드/종목명/시가총액을 수집하고,
네이버 섹터 매핑으로 업종 분류를 추가하여 stock_masters 테이블을 시딩한다.

신규 설치 시 DB 마이그레이션 직후 실행하여 Scout가 유니버스를 구성할 수 있게 한다.

Usage:
    # Dry-run (DB 저장 없이 결과 확인)
    python scripts/seed_stock_masters.py --dry-run

    # 실행 (기본 KOSPI)
    python scripts/seed_stock_masters.py

    # KOSDAQ 시딩
    python scripts/seed_stock_masters.py --market KOSDAQ
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def seed_stock_masters(market: str = "KOSPI", dry_run: bool = False) -> dict:
    """stock_masters 테이블 시딩.

    Args:
        market: "KOSPI" 또는 "KOSDAQ"
        dry_run: True면 DB 저장 없이 결과만 반환

    Returns:
        {"inserted": int, "updated": int, "total": int, "failed": int}
    """
    from prime_jennie.domain.sector_taxonomy import get_sector_group
    from prime_jennie.infra.crawlers.naver import build_naver_sector_mapping
    from prime_jennie.infra.crawlers.naver_market import fetch_market_stocks

    # 1) 네이버 시가총액 순위에서 종목 목록 + 이름 + 시총 수집
    logger.info("Fetching %s stock list from Naver...", market)
    market_stocks = fetch_market_stocks(market=market)
    logger.info("Found %d stocks", len(market_stocks))

    if not market_stocks:
        logger.error("No stocks found for market=%s", market)
        return {"inserted": 0, "updated": 0, "total": 0, "failed": 0}

    # 2) 네이버 섹터 매핑
    logger.info("Building Naver sector mapping (this takes ~30s)...")
    naver_sectors = build_naver_sector_mapping()
    logger.info("Naver sector mapping: %d stocks", len(naver_sectors))

    # 3) DB 저장 (또는 dry-run)
    if dry_run:
        inserted = 0
        for s in market_stocks:
            sector = naver_sectors.get(s.stock_code, "")
            group = get_sector_group(sector, s.stock_code).value if sector else "기타"
            cap_str = f"{s.market_cap:>10,}" if s.market_cap else "N/A"
            logger.info(
                "  %s %-20s cap=%s sector=%s group=%s",
                s.stock_code,
                s.stock_name[:20],
                cap_str,
                sector[:15] if sector else "N/A",
                group,
            )
            inserted += 1
        logger.info("DRY-RUN: %d stocks would be seeded", inserted)
        return {"inserted": inserted, "updated": 0, "total": inserted, "failed": 0}

    # 실제 DB 저장
    from sqlmodel import Session, select

    from prime_jennie.infra.database.engine import get_engine
    from prime_jennie.infra.database.models import StockMasterDB

    engine = get_engine()
    inserted = 0
    updated = 0
    failed = 0

    with Session(engine) as session:
        existing = {s.stock_code: s for s in session.exec(select(StockMasterDB)).all()}
        logger.info("Existing stocks in DB: %d", len(existing))

        for i, s in enumerate(market_stocks):
            try:
                sector = naver_sectors.get(s.stock_code, "")
                group = get_sector_group(sector, s.stock_code).value if sector else "기타"

                if s.stock_code in existing:
                    # UPDATE: 시총, 섹터 갱신
                    db_stock = existing[s.stock_code]
                    db_stock.stock_name = s.stock_name
                    if s.market_cap:
                        db_stock.market_cap = s.market_cap
                    if sector:
                        db_stock.sector_naver = sector
                        db_stock.sector_group = group
                    db_stock.is_active = True
                    db_stock.updated_at = datetime.utcnow()
                    session.add(db_stock)
                    updated += 1
                else:
                    # INSERT
                    db_stock = StockMasterDB(
                        stock_code=s.stock_code,
                        stock_name=s.stock_name,
                        market=market,
                        market_cap=s.market_cap,
                        sector_naver=sector if sector else None,
                        sector_group=group,
                        is_active=True,
                        updated_at=datetime.utcnow(),
                    )
                    session.add(db_stock)
                    inserted += 1

                # 100건마다 커밋
                if (i + 1) % 100 == 0:
                    session.commit()
                    logger.info("Progress: %d/%d", i + 1, len(market_stocks))

            except Exception as e:
                logger.warning("Failed to process %s: %s", s.stock_code, e)
                failed += 1

        session.commit()

    total = inserted + updated
    logger.info(
        "Seed complete: inserted=%d, updated=%d, total=%d, failed=%d",
        inserted,
        updated,
        total,
        failed,
    )
    return {"inserted": inserted, "updated": updated, "total": total, "failed": failed}


def main():
    parser = argparse.ArgumentParser(description="Seed stock_masters table")
    parser.add_argument("--market", default="KOSPI", choices=["KOSPI", "KOSDAQ"], help="Market to seed")
    parser.add_argument("--dry-run", action="store_true", help="Print results without DB write")
    args = parser.parse_args()

    result = seed_stock_masters(market=args.market, dry_run=args.dry_run)
    logger.info("Result: %s", result)


if __name__ == "__main__":
    main()
