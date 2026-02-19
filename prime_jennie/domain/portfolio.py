"""포트폴리오 모델."""

from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel

from .enums import SectorGroup
from .types import StockCode


class Position(BaseModel):
    """보유 포지션."""

    stock_code: StockCode
    stock_name: str
    quantity: int
    average_buy_price: int
    total_buy_amount: int
    current_price: Optional[int] = None
    current_value: Optional[int] = None
    profit_pct: Optional[float] = None
    sector_group: Optional[SectorGroup] = None
    high_watermark: Optional[int] = None  # 보유 중 최고가
    stop_loss_price: Optional[int] = None
    bought_at: Optional[datetime] = None


class PortfolioState(BaseModel):
    """포트폴리오 전체 상태."""

    positions: List[Position]
    cash_balance: int
    total_asset: int  # cash + 주식 평가액
    stock_eval_amount: int
    position_count: int
    timestamp: datetime

    @property
    def cash_ratio(self) -> float:
        if self.total_asset == 0:
            return 1.0
        return self.cash_balance / self.total_asset

    @property
    def sector_distribution(self) -> dict[str, int]:
        """섹터별 보유 종목 수."""
        dist: dict[str, int] = {}
        for p in self.positions:
            group = p.sector_group or SectorGroup.ETC
            dist[group] = dist.get(group, 0) + 1
        return dist


class DailySnapshot(BaseModel):
    """일일 자산 스냅샷."""

    snapshot_date: date
    total_asset: int
    cash_balance: int
    stock_eval_amount: int
    total_profit_loss: Optional[int] = None
    realized_profit_loss: Optional[int] = None
