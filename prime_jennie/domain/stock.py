"""종목 관련 모델."""

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel

from .enums import SectorGroup
from .types import StockCode


class StockMaster(BaseModel):
    """종목 마스터 — DB stock_masters 테이블에 대응."""
    stock_code: StockCode
    stock_name: str
    market: str = "KOSPI"                          # KOSPI | KOSDAQ
    market_cap: Optional[int] = None
    sector_naver: Optional[str] = None             # 네이버 세분류 (79개)
    sector_group: Optional[SectorGroup] = None     # 대분류 (14개)
    is_active: bool = True


class StockSnapshot(BaseModel):
    """실시간 스냅샷 — KIS API 응답."""
    stock_code: StockCode
    price: int
    open_price: int = 0
    high_price: int = 0
    low_price: int = 0
    volume: int = 0
    change_pct: float = 0.0
    per: Optional[float] = None
    pbr: Optional[float] = None
    market_cap: Optional[int] = None
    high_52w: Optional[int] = None
    low_52w: Optional[int] = None
    timestamp: datetime


class DailyPrice(BaseModel):
    """일별 OHLCV."""
    stock_code: StockCode
    price_date: date
    open_price: int
    high_price: int
    low_price: int
    close_price: int
    volume: int
    change_pct: Optional[float] = None
