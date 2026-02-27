"""Scout 섹터 예산 배정 — Percentile 기반 HOT/WARM/COOL + Council 오버라이드.

Pipeline:
  sector_analysis (14 groups) → percentile 티어 → Council 오버라이드 → SectorBudget
"""

import json
import logging
from datetime import UTC, datetime

import redis

from prime_jennie.domain.enums import SectorGroup, SectorTier
from prime_jennie.domain.sector import SectorAnalysis, SectorBudget, SectorBudgetEntry

logger = logging.getLogger(__name__)

# 티어별 기본 cap
TIER_CAPS = {
    SectorTier.HOT: {"watchlist": 5, "portfolio": 5},
    SectorTier.WARM: {"watchlist": 3, "portfolio": 3},
    SectorTier.COOL: {"watchlist": 3, "portfolio": 2},
}

REDIS_KEY = "sector_budget:active"
REDIS_TTL = 86400  # 24h


def assign_sector_tiers(
    analyses: list[SectorAnalysis],
    council_avoid: list[SectorGroup] | None = None,
    council_favor: list[SectorGroup] | None = None,
) -> dict[SectorGroup, SectorTier]:
    """Percentile 기반 HOT/WARM/COOL + Council 오버라이드.

    Rules:
        HOT:  avg_return >= p75 AND > 0%
        WARM: default
        COOL: avg_return <= p25 AND < 0% OR falling_knife_ratio >= 30%
        Council avoid → force COOL
        Council favor + COOL → upgrade to WARM
    """
    if not analyses:
        return {}

    returns = sorted(a.avg_return_pct for a in analyses)
    p25 = _percentile(returns, 25)
    p75 = _percentile(returns, 75)

    tiers: dict[SectorGroup, SectorTier] = {}

    for analysis in analyses:
        if analysis.avg_return_pct >= p75 and analysis.avg_return_pct > 0:
            tier = SectorTier.HOT
        elif analysis.avg_return_pct <= p25 and analysis.avg_return_pct < 0 or analysis.is_falling_knife:
            tier = SectorTier.COOL
        else:
            tier = SectorTier.WARM

        tiers[analysis.sector_group] = tier

    # Council overrides
    avoid = council_avoid or []
    favor = council_favor or []

    for group in avoid:
        if group in tiers:
            tiers[group] = SectorTier.COOL

    for group in favor:
        if group in tiers and tiers[group] == SectorTier.COOL:
            tiers[group] = SectorTier.WARM

    return tiers


def compute_sector_budget(
    tiers: dict[SectorGroup, SectorTier],
    held_counts: dict[SectorGroup, int] | None = None,
) -> SectorBudget:
    """티어 → SectorBudget 계산.

    effective_cap = min(watchlist_cap, max(0, portfolio_cap - held) + 1)
    """
    held = held_counts or {}
    entries: dict[SectorGroup, SectorBudgetEntry] = {}

    for group, tier in tiers.items():
        caps = TIER_CAPS[tier]
        wl_cap = caps["watchlist"]
        pf_cap = caps["portfolio"]
        current_held = held.get(group, 0)

        portfolio_room = max(0, pf_cap - current_held)
        effective = min(wl_cap, portfolio_room + 1)
        effective = max(1, effective)  # 최소 1

        entries[group] = SectorBudgetEntry(
            sector_group=group,
            tier=tier,
            watchlist_cap=wl_cap,
            portfolio_cap=pf_cap,
            effective_cap=effective,
            held_count=current_held,
        )

    return SectorBudget(
        entries=entries,
        generated_at=datetime.now(UTC).isoformat(),
        council_overrides_applied=False,
    )


def save_budget_to_redis(
    budget: SectorBudget,
    redis_client: redis.Redis,
) -> None:
    """섹터 예산을 Redis에 저장 (TTL 24h)."""
    data = {}
    for group, entry in budget.entries.items():
        data[group.value] = {
            "tier": entry.tier.value,
            "watchlist_cap": entry.watchlist_cap,
            "portfolio_cap": entry.portfolio_cap,
            "effective_cap": entry.effective_cap,
            "held_count": entry.held_count,
        }

    redis_client.set(REDIS_KEY, json.dumps(data), ex=REDIS_TTL)
    logger.info("Sector budget saved to Redis: %d sectors", len(data))


def load_budget_from_redis(redis_client: redis.Redis) -> SectorBudget | None:
    """Redis에서 섹터 예산 로드."""
    raw = redis_client.get(REDIS_KEY)
    if not raw:
        return None

    try:
        data = json.loads(raw)
        entries: dict[SectorGroup, SectorBudgetEntry] = {}

        for group_str, info in data.items():
            group = SectorGroup(group_str)
            entries[group] = SectorBudgetEntry(
                sector_group=group,
                tier=SectorTier(info["tier"]),
                watchlist_cap=info["watchlist_cap"],
                portfolio_cap=info["portfolio_cap"],
                effective_cap=info["effective_cap"],
                held_count=info.get("held_count", 0),
            )

        return SectorBudget(
            entries=entries,
            generated_at=datetime.now(UTC).isoformat(),
        )
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        logger.warning("Failed to load sector budget from Redis: %s", e)
        return None


def _percentile(sorted_values: list[float], pct: int) -> float:
    """Percentile 계산 (이미 정렬된 리스트)."""
    if not sorted_values:
        return 0.0
    k = (len(sorted_values) - 1) * pct / 100
    f = int(k)
    c = f + 1
    if c >= len(sorted_values):
        return sorted_values[-1]
    d = k - f
    return sorted_values[f] + d * (sorted_values[c] - sorted_values[f])
