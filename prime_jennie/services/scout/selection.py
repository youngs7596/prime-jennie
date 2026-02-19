"""Scout Phase 5: Watchlist Selection — Greedy 알고리즘 (섹터 cap 존중).

hybrid_score 내림차순 정렬 → 섹터 cap 체크하며 greedy 선정.
Council avoid → COOL 강제, favor → COOL→WARM 승격 (sector_budget에서 처리).
"""

import logging
from datetime import datetime, timezone

from prime_jennie.domain.enums import MarketRegime, SectorGroup, TradeTier
from prime_jennie.domain.macro import TradingContext
from prime_jennie.domain.scoring import HybridScore
from prime_jennie.domain.sector import SectorBudget
from prime_jennie.domain.watchlist import HotWatchlist, WatchlistEntry

from .enrichment import EnrichedCandidate

logger = logging.getLogger(__name__)

DEFAULT_CAP = 3  # 섹터 예산 없을 때 기본 cap


def select_watchlist(
    scores: list[HybridScore],
    candidates: dict[str, EnrichedCandidate],
    budget: SectorBudget | None,
    context: TradingContext,
    max_size: int = 20,
) -> HotWatchlist:
    """Phase 5: Greedy Selection (섹터 cap 존중).

    Algorithm:
        1. hybrid_score 내림차순 정렬
        2. BLOCKED 제외, is_tradable=True만 포함
        3. 섹터별 cap 체크하며 greedy 선정
        4. max_size 미달 시 스킵된 종목에서 backfill

    Args:
        scores: Phase 4 HybridScore 리스트
        candidates: Phase 2 EnrichedCandidate (섹터 정보)
        budget: 섹터 예산 (None이면 기본 cap=3)
        context: 트레이딩 컨텍스트
        max_size: 워치리스트 최대 크기

    Returns:
        HotWatchlist (Redis 저장 가능 형태)
    """
    # 1. 정렬 (hybrid_score 내림차순)
    sorted_scores = sorted(scores, key=lambda s: s.hybrid_score, reverse=True)

    # 2. 필터: BLOCKED 제거, tradable만
    eligible = [s for s in sorted_scores if s.trade_tier != TradeTier.BLOCKED and s.is_tradable]

    # 3. Greedy selection with sector caps
    selected: list[HybridScore] = []
    skipped: list[HybridScore] = []
    sector_counts: dict[SectorGroup, int] = {}

    for score in eligible:
        if len(selected) >= max_size:
            break

        sector = _get_sector(score.stock_code, candidates)
        cap = budget.get_cap(sector) if budget else DEFAULT_CAP

        current = sector_counts.get(sector, 0)
        if current < cap:
            selected.append(score)
            sector_counts[sector] = current + 1
        else:
            skipped.append(score)

    # 4. Backfill: max_size 미달 시 스킵된 종목 추가
    if len(selected) < max_size and skipped:
        remaining = max_size - len(selected)
        backfill = skipped[:remaining]
        selected.extend(backfill)
        logger.info("Backfilled %d stocks to reach watchlist size", len(backfill))

    # 5. WatchlistEntry 변환
    now = datetime.now(timezone.utc)
    entries = []
    for rank, score in enumerate(selected, start=1):
        sector = _get_sector(score.stock_code, candidates)
        candidate = candidates.get(score.stock_code)

        entries.append(
            WatchlistEntry(
                stock_code=score.stock_code,
                stock_name=score.stock_name,
                llm_score=score.llm_score,
                hybrid_score=score.hybrid_score,
                rank=rank,
                is_tradable=score.is_tradable,
                trade_tier=score.trade_tier,
                risk_tag=score.risk_tag,
                veto_applied=score.veto_applied,
                sector_group=sector,
                market_flow=_build_market_flow(candidate),
                scored_at=score.scored_at,
            )
        )

    watchlist = HotWatchlist(
        generated_at=now,
        market_regime=context.market_regime,
        stocks=entries,
        version=f"v{now.strftime('%Y%m%d%H%M%S')}",
    )

    logger.info(
        "Watchlist selected: %d stocks (from %d eligible, %d skipped), regime=%s",
        len(entries),
        len(eligible),
        len(skipped),
        context.market_regime,
    )
    return watchlist


def _get_sector(stock_code: str, candidates: dict[str, EnrichedCandidate]) -> SectorGroup:
    """종목의 섹터 그룹 조회."""
    candidate = candidates.get(stock_code)
    if candidate and candidate.master.sector_group:
        return candidate.master.sector_group
    return SectorGroup.ETC


def _build_market_flow(candidate: EnrichedCandidate | None) -> dict | None:
    """수급 요약 dict 생성."""
    if not candidate or not candidate.investor_trading:
        return None

    it = candidate.investor_trading
    return {
        "foreign_net": it.foreign_net_buy_sum,
        "institution_net": it.institution_net_buy_sum,
        "foreign_ratio": it.foreign_holding_ratio,
    }
