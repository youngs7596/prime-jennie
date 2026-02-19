"""섹터 예산 모델."""

from typing import Dict, Optional

from pydantic import BaseModel, Field

from .enums import SectorGroup, SectorTier


class SectorAnalysis(BaseModel):
    """섹터별 분석 결과."""

    sector_group: SectorGroup
    avg_return_pct: float
    stock_count: int
    is_falling_knife: bool = False


class SectorBudgetEntry(BaseModel):
    """개별 섹터 예산."""

    sector_group: SectorGroup
    tier: SectorTier
    watchlist_cap: int = Field(ge=0, le=10)  # Scout 선정 상한
    portfolio_cap: int = Field(ge=0, le=10)  # 포트폴리오 보유 상한
    effective_cap: int = Field(ge=0, le=10)  # 실효 상한 (보유 감안)
    held_count: int = 0  # 현재 보유 수


class SectorBudget(BaseModel):
    """전체 섹터 예산 (Redis 저장 단위)."""

    entries: Dict[SectorGroup, SectorBudgetEntry]
    generated_at: str
    council_overrides_applied: bool = False

    def get_cap(self, group: SectorGroup) -> int:
        entry = self.entries.get(group)
        return entry.effective_cap if entry else 3  # 기본 WARM cap

    def is_available(self, group: SectorGroup) -> bool:
        return self.get_cap(group) > 0
