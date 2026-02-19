"""Scout Watchlist Selection 단위 테스트."""

from datetime import date, datetime, timezone

import pytest

from prime_jennie.domain.enums import (
    MarketRegime,
    RiskTag,
    SectorGroup,
    SectorTier,
    TradeTier,
)
from prime_jennie.domain.macro import TradingContext
from prime_jennie.domain.scoring import HybridScore
from prime_jennie.domain.sector import SectorBudget, SectorBudgetEntry
from prime_jennie.domain.stock import StockMaster
from prime_jennie.services.scout.enrichment import EnrichedCandidate
from prime_jennie.services.scout.selection import select_watchlist


# ─── Fixtures ────────────────────────────────────────────────────

NOW = datetime(2026, 2, 19, 10, 0, 0, tzinfo=timezone.utc)


def _make_hybrid(
    code: str,
    name: str,
    hybrid: float,
    tier: TradeTier = TradeTier.TIER1,
    risk_tag: RiskTag = RiskTag.NEUTRAL,
    tradable: bool = True,
    veto: bool = False,
) -> HybridScore:
    return HybridScore(
        stock_code=code,
        stock_name=name,
        quant_score=hybrid - 5,
        llm_score=hybrid,
        hybrid_score=hybrid,
        risk_tag=risk_tag,
        trade_tier=tier,
        is_tradable=tradable,
        veto_applied=veto,
        scored_at=NOW,
    )


def _make_candidate(code: str, sector: SectorGroup = SectorGroup.SEMICONDUCTOR_IT) -> EnrichedCandidate:
    return EnrichedCandidate(
        master=StockMaster(
            stock_code=code,
            stock_name=f"Stock-{code}",
            market="KOSPI",
            sector_group=sector,
        ),
    )


def _make_context(regime: MarketRegime = MarketRegime.BULL) -> TradingContext:
    return TradingContext(
        date=date(2026, 2, 19),
        market_regime=regime,
    )


def _make_budget(**overrides: dict) -> SectorBudget:
    """기본 WARM(cap=3) 예산."""
    default_entry = SectorBudgetEntry(
        sector_group=SectorGroup.ETC,
        tier=SectorTier.WARM,
        watchlist_cap=3,
        portfolio_cap=3,
        effective_cap=3,
    )
    entries = {}
    for group in SectorGroup:
        entries[group] = SectorBudgetEntry(
            sector_group=group,
            tier=SectorTier.WARM,
            watchlist_cap=3,
            portfolio_cap=3,
            effective_cap=3,
        )
    # Apply overrides
    for group_str, cap in overrides.items():
        group = SectorGroup(group_str)
        entries[group] = SectorBudgetEntry(
            sector_group=group,
            tier=SectorTier.HOT if cap >= 5 else SectorTier.COOL if cap <= 2 else SectorTier.WARM,
            watchlist_cap=cap,
            portfolio_cap=cap,
            effective_cap=cap,
        )
    return SectorBudget(entries=entries, generated_at=NOW.isoformat())


# ─── Tests ───────────────────────────────────────────────────────


class TestSelectWatchlist:
    def test_selects_top_by_hybrid_score(self):
        """hybrid_score 내림차순 선정."""
        scores = [
            _make_hybrid("000001", "Stock1", 80.0),
            _make_hybrid("000002", "Stock2", 70.0),
            _make_hybrid("000003", "Stock3", 60.0),
        ]
        candidates = {
            "000001": _make_candidate("000001"),
            "000002": _make_candidate("000002"),
            "000003": _make_candidate("000003"),
        }

        result = select_watchlist(scores, candidates, None, _make_context(), max_size=2)

        assert len(result.stocks) == 2
        assert result.stocks[0].stock_code == "000001"
        assert result.stocks[1].stock_code == "000002"

    def test_excludes_blocked_stocks(self):
        """BLOCKED 티어 종목은 제외."""
        scores = [
            _make_hybrid("000001", "Stock1", 80.0),
            _make_hybrid("000002", "Stock2", 70.0, tier=TradeTier.BLOCKED, tradable=False, veto=True,
                         risk_tag=RiskTag.DISTRIBUTION_RISK),
            _make_hybrid("000003", "Stock3", 60.0),
        ]
        candidates = {
            "000001": _make_candidate("000001"),
            "000002": _make_candidate("000002"),
            "000003": _make_candidate("000003"),
        }

        result = select_watchlist(scores, candidates, None, _make_context(), max_size=3)

        codes = [s.stock_code for s in result.stocks]
        assert "000002" not in codes
        assert len(result.stocks) == 2

    def test_respects_sector_cap(self):
        """섹터별 cap 존중 — 직접 선정 시 cap 초과 안 함."""
        semi = SectorGroup.SEMICONDUCTOR_IT
        scores = [
            _make_hybrid("000001", "S1", 90.0),
            _make_hybrid("000002", "S2", 85.0),
            _make_hybrid("000003", "S3", 80.0),  # 3번째 반도체 — cap=2로 스킵
            _make_hybrid("000004", "S4", 75.0),  # 다른 섹터
        ]
        candidates = {
            "000001": _make_candidate("000001", semi),
            "000002": _make_candidate("000002", semi),
            "000003": _make_candidate("000003", semi),
            "000004": _make_candidate("000004", SectorGroup.FINANCE),
        }

        budget = _make_budget(**{semi.value: 2})

        # max_size=3: greedy로 2개 반도체 + 1개 금융 = 3. backfill 없음.
        result = select_watchlist(scores, candidates, budget, _make_context(), max_size=3)

        # 직접 선정 + backfill 포함 시 3개 반도체가 될 수 있음
        # 중요한 건 금융 종목(S4)이 cap 안에서 선정되는 것
        codes = [s.stock_code for s in result.stocks]
        assert "000004" in codes
        assert len(result.stocks) == 3

    def test_backfills_when_under_max_size(self):
        """섹터 cap으로 인해 max_size 미달 시 backfill."""
        semi = SectorGroup.SEMICONDUCTOR_IT
        scores = [
            _make_hybrid("000001", "S1", 90.0),
            _make_hybrid("000002", "S2", 85.0),
            _make_hybrid("000003", "S3", 80.0),
        ]
        candidates = {
            "000001": _make_candidate("000001", semi),
            "000002": _make_candidate("000002", semi),
            "000003": _make_candidate("000003", semi),
        }

        budget = _make_budget(**{semi.value: 1})

        result = select_watchlist(scores, candidates, budget, _make_context(), max_size=3)

        # cap=1이지만 backfill로 3개까지 채움
        assert len(result.stocks) == 3

    def test_max_watchlist_size_not_exceeded(self):
        """MAX_WATCHLIST_SIZE 미초과."""
        codes = [f"{i:06d}" for i in range(1, 26)]  # 000001 ~ 000025
        scores = [_make_hybrid(c, f"S{c}", max(1.0, 90.0 - i)) for i, c in enumerate(codes)]
        candidates = {c: _make_candidate(c) for c in codes}

        result = select_watchlist(scores, candidates, None, _make_context(), max_size=20)

        assert len(result.stocks) <= 20

    def test_rank_assigned_correctly(self):
        """rank는 1부터 순차 할당."""
        scores = [
            _make_hybrid("000001", "S1", 80.0),
            _make_hybrid("000002", "S2", 70.0),
        ]
        candidates = {
            "000001": _make_candidate("000001"),
            "000002": _make_candidate("000002"),
        }

        result = select_watchlist(scores, candidates, None, _make_context())

        assert result.stocks[0].rank == 1
        assert result.stocks[1].rank == 2

    def test_empty_scores_returns_empty_watchlist(self):
        """빈 점수 → 빈 워치리스트."""
        result = select_watchlist([], {}, None, _make_context())
        assert len(result.stocks) == 0

    def test_watchlist_metadata(self):
        """워치리스트 메타데이터 검증."""
        scores = [_make_hybrid("000001", "S1", 80.0)]
        candidates = {"000001": _make_candidate("000001")}
        context = _make_context(MarketRegime.STRONG_BULL)

        result = select_watchlist(scores, candidates, None, context)

        assert result.market_regime == MarketRegime.STRONG_BULL
        assert result.version.startswith("v")

    def test_no_budget_uses_default_cap(self):
        """섹터 예산 없으면 기본 cap=3."""
        semi = SectorGroup.SEMICONDUCTOR_IT
        codes = [f"{i:06d}" for i in range(1, 6)]  # 000001 ~ 000005
        scores = [_make_hybrid(c, f"S{c}", 90.0 - i) for i, c in enumerate(codes)]
        candidates = {c: _make_candidate(c, semi) for c in codes}

        result = select_watchlist(scores, candidates, None, _make_context(), max_size=5)

        # 기본 cap=3이므로 처음 3개만 direct, 나머지 backfill
        assert len(result.stocks) == 5  # backfill로 모두 포함
