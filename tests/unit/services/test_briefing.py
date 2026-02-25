"""Daily Briefing 서비스 단위 테스트."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ─── DailyReporter ────────────────────────────────────────────


def _make_reporter():
    with patch("prime_jennie.services.briefing.reporter.get_config"):
        from prime_jennie.services.briefing.reporter import DailyReporter

        reporter = DailyReporter.__new__(DailyReporter)
        reporter._config = MagicMock()
        reporter._telegram_token = None
        reporter._telegram_chat_id = None
        return reporter


def _empty_data(overrides: dict | None = None) -> dict:
    """기본 빈 데이터 구조."""
    data = {
        "date": "2026-02-25",
        "positions": [],
        "trades": [],
        "trade_summary": {
            "buy_count": 0,
            "sell_count": 0,
            "total_realized_pnl": 0,
            "win_count": 0,
            "loss_count": 0,
            "win_rate": 0.0,
            "best_trade": None,
            "worst_trade": None,
        },
        "macro": None,
        "watchlist": [],
        "assets": None,
        "news": [],
    }
    if overrides:
        data.update(overrides)
    return data


# ─── Fallback HTML 포맷 ──────────────────────────────────────


class TestFallbackHtmlFormat:
    """_format_fallback_html 검증."""

    def test_format_empty_data(self):
        reporter = _make_reporter()
        data = _empty_data()
        result = reporter._format_fallback_html(data)
        assert "<b>[2026-02-25] 일일 브리핑</b>" in result
        assert "매매 없음" in result

    def test_format_with_assets(self):
        reporter = _make_reporter()
        data = _empty_data(
            {
                "assets": {
                    "total_asset": 10_000_000,
                    "cash_balance": 5_000_000,
                    "stock_eval": 5_000_000,
                    "position_count": 3,
                },
            }
        )
        result = reporter._format_fallback_html(data)
        assert "10,000,000원" in result
        assert "보유 종목: 3개" in result
        assert "<b>자산 현황</b>" in result

    def test_format_with_trades(self):
        reporter = _make_reporter()
        trades = [
            {
                "stock_code": "005930",
                "stock_name": "삼성전자",
                "trade_type": "BUY",
                "quantity": 10,
                "price": 70000,
                "total_amount": 700000,
                "reason": "테스트",
                "profit_pct": None,
                "profit_amount": None,
            }
        ]
        data = _empty_data(
            {
                "trades": trades,
                "trade_summary": {
                    "buy_count": 1,
                    "sell_count": 0,
                    "total_realized_pnl": 0,
                    "win_count": 0,
                    "loss_count": 0,
                    "win_rate": 0.0,
                    "best_trade": None,
                    "worst_trade": None,
                },
            }
        )
        result = reporter._format_fallback_html(data)
        assert "매수 1건" in result
        assert "삼성전자" in result

    def test_format_with_sells_and_pnl(self):
        reporter = _make_reporter()
        trades = [
            {
                "stock_code": "005930",
                "stock_name": "삼성전자",
                "trade_type": "SELL",
                "quantity": 5,
                "price": 75000,
                "total_amount": 375000,
                "reason": "익절",
                "profit_pct": 7.1,
                "profit_amount": 25000,
            }
        ]
        data = _empty_data(
            {
                "trades": trades,
                "trade_summary": {
                    "buy_count": 0,
                    "sell_count": 1,
                    "total_realized_pnl": 25000,
                    "win_count": 1,
                    "loss_count": 0,
                    "win_rate": 100.0,
                    "best_trade": trades[0],
                    "worst_trade": trades[0],
                },
            }
        )
        result = reporter._format_fallback_html(data)
        assert "+7.1%" in result
        assert "25,000원" in result
        assert "승률: 100%" in result

    def test_format_with_macro(self):
        reporter = _make_reporter()
        data = _empty_data(
            {
                "macro": {
                    "sentiment": "BULLISH",
                    "sentiment_score": 72,
                    "regime_hint": "BULL",
                    "kospi_index": 2800.5,
                    "kospi_change_pct": 1.2,
                    "kosdaq_index": 900.3,
                    "kosdaq_change_pct": -0.5,
                    "vix_value": 18.5,
                    "vix_regime": "normal",
                    "usd_krw": 1350.0,
                    "council_consensus": "agree",
                    "risk_factors": ["인플레이션"],
                    "key_themes": ["AI"],
                    "trading_reasoning": "상승 추세",
                    "sectors_to_favor": "반도체",
                    "sectors_to_avoid": "건설",
                },
            }
        )
        result = reporter._format_fallback_html(data)
        assert "BULLISH" in result
        assert "BULL" in result
        assert "2,800.50" in result
        assert "900.30" in result
        assert "18.50" in result
        assert "1,350.0" in result

    def test_format_with_watchlist(self):
        reporter = _make_reporter()
        data = _empty_data(
            {
                "watchlist": [
                    {
                        "stock_code": "005930",
                        "stock_name": "삼성전자",
                        "hybrid_score": 82.5,
                        "trade_tier": "TIER_1",
                        "rank": 1,
                    }
                ],
            }
        )
        result = reporter._format_fallback_html(data)
        assert "<b>워치리스트</b>" in result
        assert "삼성전자" in result
        assert "82점" in result

    def test_format_with_news(self):
        reporter = _make_reporter()
        data = _empty_data(
            {
                "news": [
                    {
                        "stock_code": "005930",
                        "headline": "삼성전자 실적 발표",
                        "score": 0.85,
                    }
                ],
            }
        )
        result = reporter._format_fallback_html(data)
        assert "<b>뉴스</b>" in result
        assert "삼성전자 실적 발표" in result

    def test_html_escape_in_fallback(self):
        reporter = _make_reporter()
        data = _empty_data(
            {
                "positions": [
                    {
                        "stock_code": "TEST",
                        "stock_name": "A<B>&C",
                        "quantity": 10,
                        "avg_price": 1000,
                        "total_buy": 10000,
                    }
                ],
            }
        )
        result = reporter._format_fallback_html(data)
        assert "A&lt;B&gt;&amp;C" in result


# ─── 데이터 컨텍스트 빌더 ────────────────────────────────────


class TestBuildDataContext:
    """_build_data_context LLM 입력 텍스트 생성 검증."""

    def test_empty_data(self):
        reporter = _make_reporter()
        data = _empty_data()
        result = reporter._build_data_context(data)
        assert "[일일 브리핑 데이터] 2026-02-25" in result
        assert "자산 데이터 없음" in result
        assert "오늘 매매 없음" in result

    def test_with_full_data(self):
        reporter = _make_reporter()
        data = _empty_data(
            {
                "assets": {
                    "total_asset": 50_000_000,
                    "cash_balance": 20_000_000,
                    "stock_eval": 30_000_000,
                    "position_count": 5,
                },
                "trade_summary": {
                    "buy_count": 3,
                    "sell_count": 2,
                    "total_realized_pnl": 150000,
                    "win_count": 2,
                    "loss_count": 0,
                    "win_rate": 100.0,
                    "best_trade": {
                        "stock_name": "삼성전자",
                        "profit_pct": 5.0,
                    },
                    "worst_trade": {
                        "stock_name": "LG전자",
                        "profit_pct": 1.2,
                    },
                },
                "trades": [
                    {
                        "stock_code": "005930",
                        "stock_name": "삼성전자",
                        "trade_type": "SELL",
                        "quantity": 10,
                        "price": 75000,
                        "total_amount": 750000,
                        "reason": "익절",
                        "profit_pct": 5.0,
                        "profit_amount": 35000,
                    },
                ],
                "macro": {
                    "sentiment": "BULLISH",
                    "sentiment_score": 72,
                    "regime_hint": "BULL",
                    "kospi_index": 2800.0,
                    "kospi_change_pct": 1.5,
                    "kosdaq_index": None,
                    "kosdaq_change_pct": None,
                    "vix_value": 18.0,
                    "vix_regime": "normal",
                    "usd_krw": 1350.0,
                    "council_consensus": "agree",
                    "risk_factors": ["인플레이션"],
                    "key_themes": ["AI", "반도체"],
                    "trading_reasoning": "상승 추세",
                    "sectors_to_favor": "반도체",
                    "sectors_to_avoid": "건설",
                },
            }
        )
        result = reporter._build_data_context(data)
        assert "총자산: 50,000,000원" in result
        assert "매수: 3건 / 매도: 2건" in result
        assert "실현손익: 150,000원" in result
        assert "승률: 100%" in result
        assert "최고수익: 삼성전자 +5.0%" in result
        assert "코스피: 2,800.00 (+1.50%)" in result
        assert "핵심 테마: AI, 반도체" in result
        assert "위험 요인: 인플레이션" in result

    def test_watchlist_in_context(self):
        reporter = _make_reporter()
        data = _empty_data(
            {
                "watchlist": [
                    {
                        "stock_code": "005930",
                        "stock_name": "삼성전자",
                        "hybrid_score": 85.0,
                        "trade_tier": "TIER_1",
                        "rank": 1,
                    }
                ],
            }
        )
        result = reporter._build_data_context(data)
        assert "워치리스트 Top 1" in result
        assert "#1 삼성전자 (85점, TIER_1)" in result

    def test_news_in_context(self):
        reporter = _make_reporter()
        data = _empty_data(
            {
                "news": [
                    {
                        "stock_code": "005930",
                        "headline": "삼성전자 반도체 호황",
                        "score": 0.9,
                    }
                ],
            }
        )
        result = reporter._build_data_context(data)
        assert "주요 뉴스" in result
        assert "삼성전자 반도체 호황" in result


# ─── 매매 요약 통계 ──────────────────────────────────────────


class TestComputeTradeSummary:
    """_compute_trade_summary 검증."""

    def test_empty_trades(self):
        from prime_jennie.services.briefing.reporter import DailyReporter

        result = DailyReporter._compute_trade_summary([])
        assert result["buy_count"] == 0
        assert result["sell_count"] == 0
        assert result["win_rate"] == 0.0

    def test_mixed_trades(self):
        from prime_jennie.services.briefing.reporter import DailyReporter

        trades = [
            {
                "trade_type": "BUY",
                "profit_pct": None,
                "profit_amount": None,
            },
            {
                "trade_type": "SELL",
                "profit_pct": 5.0,
                "profit_amount": 50000,
                "stock_name": "삼성전자",
            },
            {
                "trade_type": "SELL",
                "profit_pct": -2.0,
                "profit_amount": -20000,
                "stock_name": "LG전자",
            },
        ]
        result = DailyReporter._compute_trade_summary(trades)
        assert result["buy_count"] == 1
        assert result["sell_count"] == 2
        assert result["total_realized_pnl"] == 30000
        assert result["win_count"] == 1
        assert result["loss_count"] == 1
        assert result["win_rate"] == 50.0
        assert result["best_trade"]["stock_name"] == "삼성전자"
        assert result["worst_trade"]["stock_name"] == "LG전자"


# ─── LLM 리포트 생성 ─────────────────────────────────────────


class TestLLMReport:
    """_generate_llm_report 검증."""

    @pytest.mark.asyncio
    async def test_llm_report_success(self):
        reporter = _make_reporter()

        mock_response = MagicMock()
        mock_response.content = "<b>제니의 브리핑</b>\n오늘 시장은..."

        mock_provider = AsyncMock()
        mock_provider.generate = AsyncMock(return_value=mock_response)

        with patch("prime_jennie.infra.llm.factory.LLMFactory") as mock_factory:
            mock_factory.get_provider.return_value = mock_provider

            result = await reporter._generate_llm_report("테스트 컨텍스트")
            assert result is not None
            assert "제니의 브리핑" in result

            # system prompt가 전달되었는지 확인
            call_kwargs = mock_provider.generate.call_args[1]
            assert "system" in call_kwargs
            assert "제니" in call_kwargs["system"]

    @pytest.mark.asyncio
    async def test_llm_report_failure_returns_none(self):
        reporter = _make_reporter()

        with patch(
            "prime_jennie.infra.llm.factory.LLMFactory",
        ) as mock_factory:
            mock_factory.get_provider.side_effect = Exception("no provider")

            result = await reporter._generate_llm_report("테스트 컨텍스트")
            assert result is None

    @pytest.mark.asyncio
    async def test_llm_report_empty_content_returns_none(self):
        reporter = _make_reporter()

        mock_response = MagicMock()
        mock_response.content = ""

        mock_provider = AsyncMock()
        mock_provider.generate = AsyncMock(return_value=mock_response)

        with patch("prime_jennie.infra.llm.factory.LLMFactory") as mock_factory:
            mock_factory.get_provider.return_value = mock_provider

            result = await reporter._generate_llm_report("테스트 컨텍스트")
            assert result is None


# ─── create_and_send_report 통합 ─────────────────────────────


class TestCreateAndSendReport:
    """create_and_send_report 흐름 검증."""

    @pytest.mark.asyncio
    async def test_uses_llm_report_when_available(self):
        reporter = _make_reporter()
        reporter._telegram_token = "test-token"
        reporter._telegram_chat_id = "12345"

        mock_session = MagicMock()
        data = _empty_data()

        with (
            patch.object(reporter, "collect_report_data", return_value=data),
            patch.object(
                reporter,
                "_build_data_context",
                return_value="context",
            ),
            patch.object(
                reporter,
                "_generate_llm_report",
                return_value="<b>LLM 리포트</b>",
            ) as mock_llm,
            patch.object(reporter, "_send_telegram", return_value=True) as mock_send,
        ):
            result = await reporter.create_and_send_report(mock_session)
            assert result["sent"] is True
            mock_llm.assert_called_once_with("context")
            mock_send.assert_called_once_with("<b>LLM 리포트</b>")

    @pytest.mark.asyncio
    async def test_falls_back_to_html_when_llm_fails(self):
        reporter = _make_reporter()
        reporter._telegram_token = "test-token"
        reporter._telegram_chat_id = "12345"

        mock_session = MagicMock()
        data = _empty_data()

        with (
            patch.object(reporter, "collect_report_data", return_value=data),
            patch.object(
                reporter,
                "_build_data_context",
                return_value="context",
            ),
            patch.object(
                reporter,
                "_generate_llm_report",
                return_value=None,
            ),
            patch.object(
                reporter,
                "_format_fallback_html",
                return_value="<b>Fallback</b>",
            ) as mock_fallback,
            patch.object(reporter, "_send_telegram", return_value=True) as mock_send,
        ):
            result = await reporter.create_and_send_report(mock_session)
            assert result["sent"] is True
            mock_fallback.assert_called_once_with(data)
            mock_send.assert_called_once_with("<b>Fallback</b>")


# ─── 텔레그램 발송 ───────────────────────────────────────────


class TestDailyReporterTelegram:
    """텔레그램 발송 검증."""

    def test_send_telegram_no_config(self):
        reporter = _make_reporter()
        assert reporter._send_telegram("test message") is False

    @patch("prime_jennie.services.briefing.reporter.httpx.post")
    def test_send_telegram_success(self, mock_post):
        reporter = _make_reporter()
        reporter._telegram_token = "test-token"
        reporter._telegram_chat_id = "12345"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        assert reporter._send_telegram("test message") is True
        mock_post.assert_called_once()

    @patch("prime_jennie.services.briefing.reporter.httpx.post")
    def test_send_telegram_failure(self, mock_post):
        reporter = _make_reporter()
        reporter._telegram_token = "test-token"
        reporter._telegram_chat_id = "12345"

        mock_post.side_effect = Exception("network error")

        assert reporter._send_telegram("test message") is False

    @patch("prime_jennie.services.briefing.reporter.httpx.post")
    def test_send_telegram_truncates_long_message(self, mock_post):
        reporter = _make_reporter()
        reporter._telegram_token = "test-token"
        reporter._telegram_chat_id = "12345"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        long_msg = "A" * 5000
        reporter._send_telegram(long_msg)

        call_json = mock_post.call_args[1]["json"]
        assert len(call_json["text"]) == 4096


# ─── 유틸리티 함수 ────────────────────────────────────────────


class TestUtilityFunctions:
    """_safe, _parse_json_field 검증."""

    def test_safe_escapes_html(self):
        from prime_jennie.services.briefing.reporter import _safe

        assert _safe("A<B>&C") == "A&lt;B&gt;&amp;C"
        assert _safe("normal") == "normal"

    def test_parse_json_field_valid(self):
        from prime_jennie.services.briefing.reporter import _parse_json_field

        assert _parse_json_field('["a", "b"]') == ["a", "b"]
        assert _parse_json_field('{"k": "v"}') == {"k": "v"}

    def test_parse_json_field_invalid(self):
        from prime_jennie.services.briefing.reporter import _parse_json_field

        assert _parse_json_field(None) is None
        assert _parse_json_field("") is None
        assert _parse_json_field("not json") is None
