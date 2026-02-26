"""Scout Phase 1: 투자 유니버스 로딩.

DB stock_masters 테이블에서 is_active=True인 종목을 로드.
KOSPI200 + KOSDAQ 상위 시가총액 종목으로 구성.
"""

import logging

from sqlmodel import Session

from prime_jennie.domain.config import ScoutConfig
from prime_jennie.domain.stock import StockMaster
from prime_jennie.infra.database.repositories import StockRepository

logger = logging.getLogger(__name__)


def load_universe(session: Session, config: ScoutConfig) -> dict[str, StockMaster]:
    """Phase 1: 투자 유니버스 로딩.

    Args:
        session: DB 세션
        config: Scout 설정 (universe_size)

    Returns:
        {stock_code: StockMaster} 딕셔너리 (최대 universe_size개)
    """
    db_stocks = StockRepository.get_active_stocks(session)

    if not db_stocks:
        logger.warning("No active stocks found in DB")
        return {}

    # 도메인 모델로 변환
    candidates: list[StockMaster] = []
    for db_stock in db_stocks:
        try:
            candidates.append(
                StockMaster(
                    stock_code=db_stock.stock_code,
                    stock_name=db_stock.stock_name,
                    market=db_stock.market,
                    market_cap=db_stock.market_cap,
                    sector_naver=db_stock.sector_naver,
                    sector_group=db_stock.sector_group,
                    is_active=db_stock.is_active,
                )
            )
        except Exception as e:
            logger.warning("Skipping stock %s: %s", db_stock.stock_code, e)
            continue

    # 시총 하한 필터
    before = len(candidates)
    candidates = [s for s in candidates if (s.market_cap or 0) >= config.min_market_cap]
    if before != len(candidates):
        logger.info("Market cap filter: %d → %d (min=%s)", before, len(candidates), f"{config.min_market_cap:,}")

    # 시가총액 내림차순 정렬 (None은 맨 뒤)
    candidates.sort(key=lambda s: s.market_cap or 0, reverse=True)

    # universe_size 제한
    selected = candidates[: config.universe_size]

    universe = {s.stock_code: s for s in selected}
    logger.info(
        "Universe loaded: %d stocks (from %d active, limit=%d)",
        len(universe),
        len(candidates),
        config.universe_size,
    )
    return universe
