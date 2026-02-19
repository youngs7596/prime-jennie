"""Portfolio Guard — Layer 2 포트폴리오 리스크 관리.

두 가지 체크:
1. 섹터 종목 수 제한 (동적 예산 or 고정 MAX_SECTOR_STOCKS)
2. 국면별 현금 하한선 (Cash Floor)
"""

import json
import logging
from typing import Optional

import redis

from prime_jennie.domain.config import get_config
from prime_jennie.domain.enums import MarketRegime, SectorGroup
from prime_jennie.domain.portfolio import Position

logger = logging.getLogger(__name__)

SECTOR_BUDGET_KEY = "sector_budget:active"


class GuardResult:
    """Portfolio Guard 체크 결과."""

    __slots__ = ("passed", "check_name", "reason", "details")

    def __init__(
        self, passed: bool, check_name: str, reason: str = "", details: dict | None = None
    ):
        self.passed = passed
        self.check_name = check_name
        self.reason = reason
        self.details = details or {}


class PortfolioGuard:
    """포트폴리오 레벨 리스크 가드.

    Usage:
        guard = PortfolioGuard(redis_client)
        result = guard.check_all(
            stock_code="005930",
            sector_group=SectorGroup.SEMICONDUCTOR_IT,
            buy_amount=2_000_000,
            available_cash=10_000_000,
            total_assets=50_000_000,
            positions=positions,
            regime=MarketRegime.BULL,
        )
        if not result.passed:
            # 매수 차단
            ...
    """

    def __init__(self, redis_client: Optional[redis.Redis] = None):
        self._config = get_config()
        self._redis = redis_client

    def _get_dynamic_sector_cap(self, sector_group: SectorGroup) -> Optional[int]:
        """Redis에서 동적 섹터 cap 조회."""
        if not self._config.risk.dynamic_sector_budget_enabled:
            return None
        if self._redis is None:
            return None

        try:
            raw = self._redis.hget(SECTOR_BUDGET_KEY, sector_group.value)
            if raw is None:
                return None
            data = json.loads(raw)
            return data.get("portfolio_cap")
        except Exception:
            logger.warning("Failed to load dynamic sector cap for %s", sector_group)
            return None

    def check_sector_stock_count(
        self,
        sector_group: SectorGroup,
        positions: list[Position],
    ) -> GuardResult:
        """섹터 종목 수 제한 체크."""
        # 동적 cap or 고정 cap
        dynamic_cap = self._get_dynamic_sector_cap(sector_group)
        max_allowed = dynamic_cap if dynamic_cap is not None else self._config.risk.max_sector_stocks

        # 현재 동일 섹터 보유 수
        current_count = sum(
            1 for p in positions if p.sector_group == sector_group
        )

        if current_count >= max_allowed:
            return GuardResult(
                False,
                "sector_stock_count",
                f"Sector {sector_group.value}: {current_count}/{max_allowed} (full)",
                {"sector_group": sector_group.value, "current": current_count, "max": max_allowed},
            )

        return GuardResult(
            True,
            "sector_stock_count",
            f"Sector {sector_group.value}: {current_count}/{max_allowed}",
            {"sector_group": sector_group.value, "current": current_count, "max": max_allowed},
        )

    def check_cash_floor(
        self,
        buy_amount: int,
        available_cash: int,
        total_assets: int,
        regime: MarketRegime,
    ) -> GuardResult:
        """국면별 현금 하한선 체크."""
        floor_pct = self._config.risk.get_cash_floor(regime)
        if total_assets <= 0:
            return GuardResult(True, "cash_floor", "No assets")

        cash_after = available_cash - buy_amount
        cash_after_pct = (cash_after / total_assets) * 100

        if cash_after_pct < floor_pct:
            return GuardResult(
                False,
                "cash_floor",
                f"Cash {cash_after_pct:.1f}% < floor {floor_pct:.0f}% ({regime})",
                {"cash_after_pct": cash_after_pct, "floor_pct": floor_pct},
            )

        return GuardResult(
            True,
            "cash_floor",
            f"Cash {cash_after_pct:.1f}% >= floor {floor_pct:.0f}%",
            {"cash_after_pct": cash_after_pct, "floor_pct": floor_pct},
        )

    def check_all(
        self,
        sector_group: SectorGroup,
        buy_amount: int,
        available_cash: int,
        total_assets: int,
        positions: list[Position],
        regime: MarketRegime,
    ) -> GuardResult:
        """모든 체크 순차 실행."""
        if not self._config.risk.portfolio_guard_enabled:
            logger.debug("Portfolio Guard disabled (shadow mode)")
            return GuardResult(True, "all", "Guard disabled (shadow)")

        # 1. Sector stock count
        sector_result = self.check_sector_stock_count(sector_group, positions)
        if not sector_result.passed:
            return sector_result

        # 2. Cash floor
        cash_result = self.check_cash_floor(
            buy_amount, available_cash, total_assets, regime
        )
        if not cash_result.passed:
            return cash_result

        return GuardResult(True, "all", "All checks passed")
