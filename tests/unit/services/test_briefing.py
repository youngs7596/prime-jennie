"""Daily Briefing 서비스 단위 테스트."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ─── DailyReporter ────────────────────────────────────────────


class TestDailyReporterFormatReport:
    """format_report 텍스트 포맷 검증."""

    def _make_reporter(self):
        with patch("prime_jennie.services.briefing.reporter.get_config"):
            from prime_jennie.services.briefing.reporter import DailyReporter

            reporter = DailyReporter.__new__(DailyReporter)
            reporter._config = MagicMock()
            reporter._telegram_token = None
            reporter._telegram_chat_id = None
            return reporter

    def test_format_empty_data(self):
        reporter = self._make_reporter()
        data = {
            "date": "2026-02-19",
            "positions": [],
            "trades": [],
            "macro": None,
            "watchlist": [],
            "assets": None,
            "news": [],
        }
        result = reporter.format_report(data)
        assert "[Daily Briefing] 2026-02-19" in result

    def test_format_with_assets(self):
        reporter = self._make_reporter()
        data = {
            "date": "2026-02-19",
            "positions": [],
            "trades": [],
            "macro": None,
            "watchlist": [],
            "assets": {
                "total_asset": 10_000_000,
                "cash_balance": 5_000_000,
                "stock_eval": 5_000_000,
                "position_count": 3,
            },
            "news": [],
        }
        result = reporter.format_report(data)
        assert "10,000,000원" in result
        assert "보유 종목: 3개" in result

    def test_format_with_trades(self):
        reporter = self._make_reporter()
        data = {
            "date": "2026-02-19",
            "positions": [],
            "trades": [
                {
                    "stock_code": "005930",
                    "stock_name": "삼성전자",
                    "trade_type": "BUY",
                    "quantity": 10,
                    "price": 70000,
                    "reason": "테스트",
                    "profit_pct": None,
                }
            ],
            "macro": None,
            "watchlist": [],
            "assets": None,
            "news": [],
        }
        result = reporter.format_report(data)
        assert "[매매] 오늘 1건" in result
        assert "삼성전자" in result

    def test_format_with_macro(self):
        reporter = self._make_reporter()
        data = {
            "date": "2026-02-19",
            "positions": [],
            "trades": [],
            "macro": {
                "sentiment": "BULLISH",
                "sentiment_score": 72,
                "regime_hint": "BULL",
                "sectors_to_favor": "반도체",
                "sectors_to_avoid": "건설",
            },
            "watchlist": [],
            "assets": None,
            "news": [],
        }
        result = reporter.format_report(data)
        assert "BULLISH" in result
        assert "BULL" in result

    def test_format_with_watchlist(self):
        reporter = self._make_reporter()
        data = {
            "date": "2026-02-19",
            "positions": [],
            "trades": [],
            "macro": None,
            "watchlist": [
                {
                    "stock_code": "005930",
                    "stock_name": "삼성전자",
                    "hybrid_score": 82.5,
                    "trade_tier": "TIER_1",
                    "rank": 1,
                }
            ],
            "assets": None,
            "news": [],
        }
        result = reporter.format_report(data)
        assert "[워치리스트]" in result
        assert "삼성전자" in result
        assert "82점" in result  # 82.5 → 82 (:.0f truncation)

    def test_format_trades_with_profit(self):
        reporter = self._make_reporter()
        data = {
            "date": "2026-02-19",
            "positions": [],
            "trades": [
                {
                    "stock_code": "005930",
                    "stock_name": "삼성전자",
                    "trade_type": "SELL",
                    "quantity": 5,
                    "price": 75000,
                    "reason": "익절",
                    "profit_pct": 7.1,
                }
            ],
            "macro": None,
            "watchlist": [],
            "assets": None,
            "news": [],
        }
        result = reporter.format_report(data)
        assert "+7.1%" in result


class TestDailyReporterTelegram:
    """텔레그램 발송 검증."""

    def test_send_telegram_no_config(self):
        with patch("prime_jennie.services.briefing.reporter.get_config"):
            from prime_jennie.services.briefing.reporter import DailyReporter

            reporter = DailyReporter.__new__(DailyReporter)
            reporter._config = MagicMock()
            reporter._telegram_token = None
            reporter._telegram_chat_id = None

            assert reporter._send_telegram("test message") is False

    @patch("prime_jennie.services.briefing.reporter.httpx.post")
    def test_send_telegram_success(self, mock_post):
        with patch("prime_jennie.services.briefing.reporter.get_config"):
            from prime_jennie.services.briefing.reporter import DailyReporter

            reporter = DailyReporter.__new__(DailyReporter)
            reporter._config = MagicMock()
            reporter._telegram_token = "test-token"
            reporter._telegram_chat_id = "12345"

            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_post.return_value = mock_resp

            assert reporter._send_telegram("test message") is True
            mock_post.assert_called_once()

    @patch("prime_jennie.services.briefing.reporter.httpx.post")
    def test_send_telegram_failure(self, mock_post):
        with patch("prime_jennie.services.briefing.reporter.get_config"):
            from prime_jennie.services.briefing.reporter import DailyReporter

            reporter = DailyReporter.__new__(DailyReporter)
            reporter._config = MagicMock()
            reporter._telegram_token = "test-token"
            reporter._telegram_chat_id = "12345"

            mock_post.side_effect = Exception("network error")

            assert reporter._send_telegram("test message") is False

    @patch("prime_jennie.services.briefing.reporter.httpx.post")
    def test_send_telegram_truncates_long_message(self, mock_post):
        with patch("prime_jennie.services.briefing.reporter.get_config"):
            from prime_jennie.services.briefing.reporter import DailyReporter

            reporter = DailyReporter.__new__(DailyReporter)
            reporter._config = MagicMock()
            reporter._telegram_token = "test-token"
            reporter._telegram_chat_id = "12345"

            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_post.return_value = mock_resp

            long_msg = "A" * 5000
            reporter._send_telegram(long_msg)

            call_json = mock_post.call_args[1]["json"]
            assert len(call_json["text"]) == 4096


class TestDailyReporterLLM:
    """LLM 요약 생성 검증."""

    @pytest.mark.asyncio
    async def test_llm_summary_success(self):
        with patch("prime_jennie.services.briefing.reporter.get_config"):
            from prime_jennie.services.briefing.reporter import DailyReporter

            reporter = DailyReporter.__new__(DailyReporter)
            reporter._config = MagicMock()

            mock_response = MagicMock()
            mock_response.content = "요약 결과"

            mock_provider = AsyncMock()
            mock_provider.generate = AsyncMock(return_value=mock_response)

            with patch("prime_jennie.infra.llm.factory.LLMFactory") as mock_factory:
                mock_factory.get_provider.return_value = mock_provider

                result = await reporter._generate_llm_summary("원본 리포트")
                assert result is not None
                assert "[AI 요약]" in result
                assert "요약 결과" in result

    @pytest.mark.asyncio
    async def test_llm_summary_failure_returns_none(self):
        with patch("prime_jennie.services.briefing.reporter.get_config"):
            from prime_jennie.services.briefing.reporter import DailyReporter

            reporter = DailyReporter.__new__(DailyReporter)
            reporter._config = MagicMock()

            with patch(
                "prime_jennie.infra.llm.factory.LLMFactory",
            ) as mock_factory:
                mock_factory.get_provider.side_effect = Exception("no provider")

                result = await reporter._generate_llm_summary("원본 리포트")
                assert result is None
