"""Stock Master 초기 시딩 스크립트.

pykrx로 KOSPI(또는 KOSDAQ) 전 종목의 코드/종목명/시가총액을 수집하고,
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
from datetime import date, datetime
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
    from pykrx import stock as pykrx_stock

    from prime_jennie.domain.sector_taxonomy import get_sector_group
    from prime_jennie.infra.crawlers.naver import build_naver_sector_mapping

    # 1) pykrx로 종목 목록 수집
    today_str = date.today().strftime("%Y%m%d")
    logger.info("Fetching %s ticker list from pykrx...", market)
    tickers = pykrx_stock.get_market_ticker_list(today_str, market=market)
    logger.info("Found %d tickers", len(tickers))

    if not tickers:
        logger.error("No tickers found for market=%s", market)
        return {"inserted": 0, "updated": 0, "total": 0, "failed": 0}

    # 2) 종목명 수집
    ticker_names: dict[str, str] = {}
    for ticker in tickers:
        name = pykrx_stock.get_market_ticker_name(ticker)
        ticker_names[ticker] = name

    # 3) 시가총액 수집 (전체 시장 한 번에)
    logger.info("Fetching market cap data...")
    df_cap = pykrx_stock.get_market_cap_by_ticker(today_str, market=market)
    market_caps: dict[str, int] = {}
    if df_cap is not None and not df_cap.empty:
        for code in df_cap.index:
            cap = df_cap.loc[code, "시가총액"]
            if cap and cap > 0:
                # 백만원 단위로 저장 (기존 DB 컨벤션)
                market_caps[code] = int(cap / 1_000_000)

    # 4) 네이버 섹터 매핑
    logger.info("Building Naver sector mapping (this takes ~30s)...")
    naver_sectors = build_naver_sector_mapping()
    logger.info("Naver sector mapping: %d stocks", len(naver_sectors))

    # 5) DB 저장 (또는 dry-run)
    if dry_run:
        inserted = 0
        for ticker in tickers:
            name = ticker_names.get(ticker, "")
            cap = market_caps.get(ticker)
            sector = naver_sectors.get(ticker, "")
            group = get_sector_group(sector, ticker).value if sector else "기타"
            cap_str = f"{cap:>10,}" if cap else "N/A"
            logger.info(
                "  %s %-20s cap=%s sector=%s group=%s",
                ticker,
                name[:20],
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

        for i, ticker in enumerate(tickers):
            try:
                name = ticker_names.get(ticker, "")
                if not name:
                    continue

                cap = market_caps.get(ticker)
                sector = naver_sectors.get(ticker, "")
                group = get_sector_group(sector, ticker).value if sector else "기타"

                if ticker in existing:
                    # UPDATE: 시총, 섹터 갱신
                    db_stock = existing[ticker]
                    db_stock.stock_name = name
                    if cap:
                        db_stock.market_cap = cap
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
                        stock_code=ticker,
                        stock_name=name,
                        market=market,
                        market_cap=cap,
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
                    logger.info("Progress: %d/%d", i + 1, len(tickers))

            except Exception as e:
                logger.warning("Failed to process %s: %s", ticker, e)
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
