"""Telegram Command Handler 단위 테스트."""

import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest


# ─── TelegramBot ──────────────────────────────────────────


class TestTelegramBot:
    def _make_bot(self, allowed="123,456"):
        from prime_jennie.services.telegram.bot import TelegramBot

        return TelegramBot(token="test-token", allowed_chat_ids=allowed)

    def test_is_authorized_allowed(self):
        bot = self._make_bot("123,456")
        assert bot.is_authorized(123) is True
        assert bot.is_authorized("456") is True

    def test_is_authorized_denied(self):
        bot = self._make_bot("123")
        assert bot.is_authorized(999) is False

    def test_is_authorized_no_whitelist(self):
        bot = self._make_bot("")
        assert bot.is_authorized(999) is True

    def test_parse_command_basic(self):
        from prime_jennie.services.telegram.bot import TelegramBot

        cmd, args = TelegramBot.parse_command("/buy 삼성전자 10")
        assert cmd == "/buy"
        assert args == "삼성전자 10"

    def test_parse_command_no_args(self):
        from prime_jennie.services.telegram.bot import TelegramBot

        cmd, args = TelegramBot.parse_command("/help")
        assert cmd == "/help"
        assert args == ""

    def test_parse_command_with_botname(self):
        from prime_jennie.services.telegram.bot import TelegramBot

        cmd, args = TelegramBot.parse_command("/status@MyBot")
        assert cmd == "/status"

    def test_parse_command_non_command(self):
        from prime_jennie.services.telegram.bot import TelegramBot

        cmd, args = TelegramBot.parse_command("hello")
        assert cmd is None
        assert args == ""

    def test_parse_command_empty(self):
        from prime_jennie.services.telegram.bot import TelegramBot

        cmd, args = TelegramBot.parse_command("")
        assert cmd is None

    @patch("prime_jennie.services.telegram.bot.httpx.post")
    def test_send_message_success(self, mock_post):
        bot = self._make_bot()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        assert bot.send_message("123", "hello") is True
        mock_post.assert_called_once()

    @patch("prime_jennie.services.telegram.bot.httpx.post")
    def test_send_message_truncates(self, mock_post):
        bot = self._make_bot()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        bot.send_message("123", "A" * 5000)
        call_json = mock_post.call_args[1]["json"]
        assert len(call_json["text"]) == 4096

    @patch("prime_jennie.services.telegram.bot.httpx.post")
    def test_send_message_failure(self, mock_post):
        bot = self._make_bot()
        mock_post.side_effect = Exception("network")

        assert bot.send_message("123", "test") is False


# ─── CommandHandler ───────────────────────────────────────


class TestCommandHandler:
    @patch("prime_jennie.services.telegram.handler.get_config")
    def _make_handler(self, mock_config):
        from prime_jennie.services.telegram.handler import CommandHandler

        mock_config.return_value = MagicMock(
            trading_mode="MOCK",
            risk=MagicMock(max_portfolio_size=10, max_buy_count_per_day=6),
        )
        mock_redis = MagicMock()
        mock_redis.exists.return_value = False
        mock_redis.get.return_value = None

        mock_kis = MagicMock()
        mock_session_factory = MagicMock()

        handler = CommandHandler(mock_redis, mock_kis, mock_session_factory)
        return handler, mock_redis, mock_kis, mock_session_factory

    def test_help(self):
        handler, *_ = self._make_handler()
        result = handler.process_command("/help", "", "123")
        assert "Prime Jennie" in result
        assert "/buy" in result

    def test_unknown_command(self):
        handler, *_ = self._make_handler()
        result = handler.process_command("/unknown", "", "123")
        assert "알 수 없는 명령" in result

    def test_rate_limited(self):
        handler, mock_redis, *_ = self._make_handler()
        mock_redis.exists.return_value = True  # already rate-limited

        result = handler.process_command("/help", "", "123")
        assert "너무 빠릅니다" in result

    def test_status(self):
        handler, mock_redis, *_ = self._make_handler()
        mock_redis.get.return_value = None

        result = handler.process_command("/status", "", "123")
        assert "시스템 상태" in result
        assert "MOCK" in result

    def test_pause(self):
        handler, mock_redis, *_ = self._make_handler()
        result = handler.process_command("/pause", "점검중", "123")
        assert "일시정지" in result
        mock_redis.set.assert_called()

    def test_resume(self):
        handler, mock_redis, *_ = self._make_handler()
        result = handler.process_command("/resume", "", "123")
        assert "재개" in result
        mock_redis.delete.assert_called()

    def test_stop_requires_confirmation(self):
        handler, *_ = self._make_handler()
        result = handler.process_command("/stop", "", "123")
        assert "확인" in result

    def test_stop_with_confirmation(self):
        handler, mock_redis, *_ = self._make_handler()
        result = handler.process_command("/stop", "확인", "123")
        assert "긴급 정지" in result

    def test_dryrun_on(self):
        handler, mock_redis, *_ = self._make_handler()
        result = handler.process_command("/dryrun", "on", "123")
        assert "ON" in result

    def test_dryrun_off(self):
        handler, mock_redis, *_ = self._make_handler()
        result = handler.process_command("/dryrun", "off", "123")
        assert "OFF" in result

    def test_dryrun_invalid(self):
        handler, *_ = self._make_handler()
        result = handler.process_command("/dryrun", "maybe", "123")
        assert "사용법" in result

    def test_balance(self):
        handler, _, mock_kis, _ = self._make_handler()
        mock_kis.get_cash_balance.return_value = 5000000

        result = handler.process_command("/balance", "", "123")
        assert "5,000,000" in result

    def test_mute(self):
        handler, mock_redis, *_ = self._make_handler()
        result = handler.process_command("/mute", "30", "123")
        assert "30분" in result
        mock_redis.set.assert_called()

    def test_mute_invalid(self):
        handler, *_ = self._make_handler()
        result = handler.process_command("/mute", "abc", "123")
        assert "사용법" in result

    def test_unmute(self):
        handler, mock_redis, *_ = self._make_handler()
        result = handler.process_command("/unmute", "", "123")
        assert "재개" in result

    def test_maxbuy(self):
        handler, mock_redis, *_ = self._make_handler()
        result = handler.process_command("/maxbuy", "5", "123")
        assert "5회" in result

    def test_maxbuy_out_of_range(self):
        handler, *_ = self._make_handler()
        result = handler.process_command("/maxbuy", "99", "123")
        assert "0~20" in result

    def test_config(self):
        handler, mock_redis, *_ = self._make_handler()
        mock_redis.get.return_value = None

        result = handler.process_command("/config", "", "123")
        assert "현재 설정" in result

    def test_sellall_requires_confirmation(self):
        handler, *_ = self._make_handler()
        result = handler.process_command("/sellall", "", "123")
        assert "확인" in result

    def test_sellall_with_confirmation(self):
        handler, mock_redis, *_ = self._make_handler()
        result = handler.process_command("/sellall", "확인", "123")
        assert "청산 요청" in result
        mock_redis.xadd.assert_called_once()

    def test_diagnose(self):
        handler, mock_redis, mock_kis, mock_sf = self._make_handler()
        mock_redis.ping.return_value = True
        mock_session = MagicMock()
        mock_sf.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_sf.return_value.__exit__ = MagicMock(return_value=False)
        mock_session.exec.return_value.first.return_value = None
        mock_kis.get_cash_balance.return_value = 1000000

        result = handler.process_command("/diagnose", "", "123")
        assert "시스템 진단" in result

    def test_buy_no_args(self):
        handler, *_ = self._make_handler()
        result = handler.process_command("/buy", "", "123")
        assert "사용법" in result

    def test_sell_no_args(self):
        handler, *_ = self._make_handler()
        result = handler.process_command("/sell", "", "123")
        assert "사용법" in result

    def test_price_no_args(self):
        handler, *_ = self._make_handler()
        result = handler.process_command("/price", "", "123")
        assert "사용법" in result

    def test_alerts_empty(self):
        handler, mock_redis, *_ = self._make_handler()
        mock_redis.hgetall.return_value = {}

        result = handler.process_command("/alerts", "", "123")
        assert "설정된 알림이 없습니다" in result

    def test_manual_trade_limit(self):
        handler, mock_redis, *_ = self._make_handler()
        # Set manual trade count to limit
        mock_redis.get.side_effect = lambda key: (
            str(LIMIT) if "manual_trades" in key else None
        )

        LIMIT = 20
        # Directly test the limit checker
        assert handler._check_manual_trade_limit("123") is False

    def test_resolve_stock_by_code(self):
        handler, _, _, mock_sf = self._make_handler()
        mock_session = MagicMock()
        mock_sf.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_sf.return_value.__exit__ = MagicMock(return_value=False)

        mock_stock = MagicMock()
        mock_stock.stock_code = "005930"
        mock_stock.stock_name = "삼성전자"
        mock_session.exec.return_value.first.return_value = mock_stock

        result = handler._resolve_stock("005930")
        assert result == ("005930", "삼성전자")
