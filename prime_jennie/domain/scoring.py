"""스코어링 모델 — Quant, LLM, Hybrid 평가 결과."""

from datetime import datetime
from typing import Self

from pydantic import BaseModel, field_validator, model_validator

from .enums import RiskTag, TradeTier
from .types import Score, StockCode


class QuantScore(BaseModel):
    """Quant Scorer v2 출력 — 6개 서브팩터 합산."""

    stock_code: StockCode
    stock_name: str
    total_score: Score
    momentum_score: float = 0.0  # 0-20
    quality_score: float = 0.0  # 0-20
    value_score: float = 0.0  # 0-20
    technical_score: float = 0.0  # 0-10
    news_score: float = 0.0  # 0-10
    supply_demand_score: float = 0.0  # 0-20
    matched_conditions: list[str] = []
    condition_win_rate: float | None = None
    condition_confidence: str | None = None  # LOW | MID | HIGH
    is_valid: bool = True
    invalid_reason: str | None = None

    @model_validator(mode="after")
    def check_total_vs_subscores(self) -> Self:
        expected = (
            self.momentum_score
            + self.quality_score
            + self.value_score
            + self.technical_score
            + self.news_score
            + self.supply_demand_score
        )
        if abs(self.total_score - expected) > 1.5:
            raise ValueError(
                f"total_score({self.total_score:.1f}) != subscores sum({expected:.1f}), "
                f"diff={abs(self.total_score - expected):.1f}"
            )
        return self


class LLMAnalysis(BaseModel):
    """LLM Analyst 출력 — Unified Analyst Pipeline."""

    stock_code: StockCode
    raw_score: Score  # LLM이 반환한 원시 점수
    clamped_score: Score  # ±15pt 클램핑 적용 후
    grade: str  # S / A / B / C / D
    reason: str
    scored_at: datetime

    @field_validator("reason")
    @classmethod
    def reason_not_too_short(cls, v: str) -> str:
        if len(v.strip()) < 10:
            raise ValueError("reason must be at least 10 characters")
        return v


class HybridScore(BaseModel):
    """최종 평가 — Quant + LLM 통합 결과."""

    stock_code: StockCode
    stock_name: str
    quant_score: Score
    llm_score: Score
    hybrid_score: Score  # = clamped llm_score
    risk_tag: RiskTag
    trade_tier: TradeTier
    is_tradable: bool
    veto_applied: bool = False
    scored_at: datetime

    @model_validator(mode="after")
    def check_business_rules(self) -> Self:
        if self.trade_tier == TradeTier.BLOCKED and self.is_tradable:
            raise ValueError("trade_tier=BLOCKED must have is_tradable=False")
        if self.risk_tag == RiskTag.DISTRIBUTION_RISK and not self.veto_applied:
            raise ValueError("risk_tag=DISTRIBUTION_RISK must have veto_applied=True")
        return self
