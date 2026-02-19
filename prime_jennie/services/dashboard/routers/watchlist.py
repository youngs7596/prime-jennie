"""Watchlist API — 현재 워치리스트 + DB 히스토리."""

import redis
from fastapi import APIRouter, Depends
from sqlmodel import Session

from prime_jennie.domain.watchlist import HotWatchlist
from prime_jennie.infra.database.repositories import WatchlistRepository
from prime_jennie.infra.redis.cache import TypedCache
from prime_jennie.services.deps import get_db_session, get_redis_client

router = APIRouter(prefix="/watchlist", tags=["watchlist"])

WATCHLIST_CACHE_KEY = "watchlist:active"


@router.get("/current")
def get_current(r: redis.Redis = Depends(get_redis_client)) -> dict:
    """Redis에서 현재 활성 워치리스트 조회."""
    cache = TypedCache(r, WATCHLIST_CACHE_KEY, HotWatchlist)
    watchlist = cache.get()
    if watchlist:
        return watchlist.model_dump(mode="json")
    return {"status": "no_data", "stocks": []}


@router.get("/history")
def get_history(session: Session = Depends(get_db_session)) -> list[dict]:
    """DB에서 최근 워치리스트 히스토리 조회."""
    entries = WatchlistRepository.get_latest(session)
    return [
        {
            "snapshot_date": e.snapshot_date.isoformat(),
            "stock_code": e.stock_code,
            "stock_name": e.stock_name,
            "llm_score": e.llm_score,
            "hybrid_score": e.hybrid_score,
            "is_tradable": e.is_tradable,
            "trade_tier": e.trade_tier,
            "risk_tag": e.risk_tag,
            "rank": e.rank,
        }
        for e in entries
    ]
