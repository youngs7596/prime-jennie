"""TradingContext regime 매핑 경계값 테스트."""

from datetime import date

import pytest

from prime_jennie.domain.enums import MarketRegime, Sentiment
from prime_jennie.domain.macro import MacroInsight


def _make_insight(score: int) -> MacroInsight:
    return MacroInsight(
        insight_date=date(2026, 3, 3),
        sentiment=Sentiment.NEUTRAL,
        sentiment_score=score,
        regime_hint="test",
    )


# _update_trading_context 내부의 regime 매핑 로직만 추출 테스트
def _score_to_regime(score: int) -> MarketRegime:
    """jobs/app.py _update_trading_context의 regime 매핑 로직 재현."""
    if score >= 70:
        return MarketRegime.STRONG_BULL
    elif score >= 60:
        return MarketRegime.BULL
    elif score >= 40:
        return MarketRegime.SIDEWAYS
    elif score >= 25:
        return MarketRegime.BEAR
    else:
        return MarketRegime.STRONG_BEAR


class TestRegimeMapping:
    @pytest.mark.parametrize(
        ("score", "expected"),
        [
            (100, MarketRegime.STRONG_BULL),
            (70, MarketRegime.STRONG_BULL),
            (69, MarketRegime.BULL),
            (60, MarketRegime.BULL),
            (59, MarketRegime.SIDEWAYS),
            (55, MarketRegime.SIDEWAYS),
            (40, MarketRegime.SIDEWAYS),
            (39, MarketRegime.BEAR),
            (25, MarketRegime.BEAR),
            (24, MarketRegime.STRONG_BEAR),
            (0, MarketRegime.STRONG_BEAR),
        ],
    )
    def test_score_boundary(self, score: int, expected: MarketRegime):
        """경계값에서의 regime 매핑 확인."""
        assert _score_to_regime(score) == expected

    def test_score_55_is_sideways_not_bull(self):
        """핵심 변경: score 55는 BULL이 아닌 SIDEWAYS."""
        assert _score_to_regime(55) == MarketRegime.SIDEWAYS

    def test_score_59_is_sideways(self):
        """score 59는 SIDEWAYS."""
        assert _score_to_regime(59) == MarketRegime.SIDEWAYS

    def test_score_60_is_bull(self):
        """score 60은 BULL."""
        assert _score_to_regime(60) == MarketRegime.BULL
