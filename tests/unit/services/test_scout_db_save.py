"""Scout _save_watchlist_to_db 단위 테스트."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from prime_jennie.domain.enums import (
    MarketRegime,
    RiskTag,
    SectorGroup,
    TradeTier,
)
from prime_jennie.domain.watchlist import HotWatchlist, WatchlistEntry
from prime_jennie.infra.database.models import WatchlistHistoryDB
from prime_jennie.services.scout.app import _save_watchlist_to_db

NOW = datetime(2026, 2, 25, 10, 0, 0, tzinfo=UTC)


def _make_watchlist(entries: list[WatchlistEntry], regime: MarketRegime = MarketRegime.SIDEWAYS) -> HotWatchlist:
    return HotWatchlist(
        generated_at=NOW,
        market_regime=regime,
        stocks=entries,
        version="v20260225",
    )


def _make_entry(
    code: str = "005930",
    name: str = "삼성전자",
    quant_score: float = 72.0,
    llm_score: float = 80.0,
    hybrid_score: float = 76.0,
    rank: int = 1,
    sector_group: SectorGroup | None = SectorGroup.SEMICONDUCTOR_IT,
    risk_tag: RiskTag = RiskTag.NEUTRAL,
    trade_tier: TradeTier = TradeTier.TIER1,
) -> WatchlistEntry:
    return WatchlistEntry(
        stock_code=code,
        stock_name=name,
        quant_score=quant_score,
        llm_score=llm_score,
        hybrid_score=hybrid_score,
        rank=rank,
        is_tradable=True,
        trade_tier=trade_tier,
        risk_tag=risk_tag,
        sector_group=sector_group,
        scored_at=NOW,
    )


# ─── Tests ────────────────────────────────────────────────────────


class TestSaveWatchlistToDb:
    """_save_watchlist_to_db 컬럼 매핑 검증."""

    @patch("prime_jennie.services.scout.app.WatchlistRepository")
    @patch("prime_jennie.services.scout.app.date")
    def test_new_columns_mapped(self, mock_date, mock_repo):
        """quant_score, sector_group, market_regime 이 DB 엔트리에 매핑되는지 확인."""
        mock_date.today.return_value = mock_date
        mock_date.__eq__ = lambda self, other: True

        session = MagicMock()
        entry = _make_entry(
            quant_score=72.5,
            sector_group=SectorGroup.DEFENSE_SHIPBUILDING,
        )
        watchlist = _make_watchlist([entry], regime=MarketRegime.BULL)

        _save_watchlist_to_db(session, watchlist)

        # replace_history 호출 확인
        mock_repo.replace_history.assert_called_once()
        _session, _date, entries = mock_repo.replace_history.call_args[0]

        assert len(entries) == 1
        db_entry: WatchlistHistoryDB = entries[0]
        assert db_entry.quant_score == 72.5
        assert db_entry.sector_group == "조선/방산"
        assert db_entry.market_regime == "BULL"

    @patch("prime_jennie.services.scout.app.WatchlistRepository")
    @patch("prime_jennie.services.scout.app.date")
    def test_none_sector_group(self, mock_date, mock_repo):
        """sector_group이 None일 때 DB에 None으로 저장."""
        mock_date.today.return_value = mock_date
        mock_date.__eq__ = lambda self, other: True

        session = MagicMock()
        entry = _make_entry(sector_group=None)
        watchlist = _make_watchlist([entry])

        _save_watchlist_to_db(session, watchlist)

        _session, _date, entries = mock_repo.replace_history.call_args[0]
        db_entry: WatchlistHistoryDB = entries[0]
        assert db_entry.sector_group is None
        assert db_entry.market_regime == "SIDEWAYS"

    @patch("prime_jennie.services.scout.app.WatchlistRepository")
    @patch("prime_jennie.services.scout.app.date")
    def test_db_error_triggers_rollback(self, mock_date, mock_repo):
        """DB 저장 실패 시 session.rollback() 호출 확인."""
        mock_date.today.return_value = mock_date
        mock_date.__eq__ = lambda self, other: True

        session = MagicMock()
        mock_repo.replace_history.side_effect = RuntimeError("DB error")

        watchlist = _make_watchlist([_make_entry()])

        # 예외가 전파되지 않고 내부에서 처리됨
        _save_watchlist_to_db(session, watchlist)

        session.rollback.assert_called_once()

    @patch("prime_jennie.services.scout.app.WatchlistRepository")
    @patch("prime_jennie.services.scout.app.date")
    def test_multiple_entries_all_mapped(self, mock_date, mock_repo):
        """여러 종목의 컬럼이 모두 올바르게 매핑되는지 확인."""
        mock_date.today.return_value = mock_date
        mock_date.__eq__ = lambda self, other: True

        session = MagicMock()
        entries = [
            _make_entry(code="005930", rank=1, sector_group=SectorGroup.SEMICONDUCTOR_IT),
            _make_entry(code="000660", name="SK하이닉스", rank=2, sector_group=SectorGroup.SEMICONDUCTOR_IT),
            _make_entry(code="035420", name="NAVER", rank=3, sector_group=SectorGroup.MEDIA_ENTERTAINMENT),
        ]
        watchlist = _make_watchlist(entries, regime=MarketRegime.BEAR)

        _save_watchlist_to_db(session, watchlist)

        _session, _date, db_entries = mock_repo.replace_history.call_args[0]
        assert len(db_entries) == 3
        assert db_entries[0].sector_group == "반도체/IT"
        assert db_entries[2].sector_group == "미디어/엔터"
        # 모든 엔트리의 market_regime이 동일 (watchlist 레벨)
        assert all(e.market_regime == "BEAR" for e in db_entries)
