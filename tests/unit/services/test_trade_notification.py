"""매수/매도 체결 텔레그램 알림 단위 테스트."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from prime_jennie.domain.notification import TradeNotification

# ─── TradeNotification 모델 ─────────────────────────────────


class TestTradeNotification:
    def _make_buy_notification(self, **overrides):
        defaults = {
            "trade_type": "BUY",
            "stock_code": "005930",
            "stock_name": "삼성전자",
            "quantity": 100,
            "price": 70000,
            "total_amount": 7000000,
            "signal_type": "GOLDEN_CROSS",
            "trade_tier": "TIER1",
            "hybrid_score": 82.5,
            "timestamp": datetime(2026, 2, 21, 9, 30, 0, tzinfo=UTC),
        }
        defaults.update(overrides)
        return TradeNotification(**defaults)

    def _make_sell_notification(self, **overrides):
        defaults = {
            "trade_type": "SELL",
            "stock_code": "005930",
            "stock_name": "삼성전자",
            "quantity": 100,
            "price": 80000,
            "total_amount": 8000000,
            "sell_reason": "PROFIT_TARGET",
            "profit_pct": 14.29,
            "holding_days": 5,
            "timestamp": datetime(2026, 2, 21, 14, 30, 0, tzinfo=UTC),
        }
        defaults.update(overrides)
        return TradeNotification(**defaults)

    def test_buy_notification_serialization(self):
        n = self._make_buy_notification()
        json_str = n.model_dump_json()
        restored = TradeNotification.model_validate_json(json_str)
        assert restored.trade_type == "BUY"
        assert restored.stock_code == "005930"
        assert restored.quantity == 100
        assert restored.signal_type == "GOLDEN_CROSS"
        assert restored.sell_reason is None

    def test_sell_notification_serialization(self):
        n = self._make_sell_notification()
        json_str = n.model_dump_json()
        restored = TradeNotification.model_validate_json(json_str)
        assert restored.trade_type == "SELL"
        assert restored.profit_pct == 14.29
        assert restored.sell_reason == "PROFIT_TARGET"
        assert restored.signal_type is None

    def test_buy_optional_fields_default_none(self):
        n = TradeNotification(
            trade_type="BUY",
            stock_code="005930",
            stock_name="삼성전자",
            quantity=10,
            price=70000,
            total_amount=700000,
            timestamp=datetime.now(UTC),
        )
        assert n.signal_type is None
        assert n.trade_tier is None
        assert n.hybrid_score is None
        assert n.sell_reason is None


# ─── _format_trade_message ──────────────────────────────────


class TestFormatTradeMessage:
    def test_buy_message_format(self):
        from prime_jennie.services.telegram.app import _format_trade_message

        n = TradeNotification(
            trade_type="BUY",
            stock_code="005930",
            stock_name="삼성전자",
            quantity=100,
            price=70000,
            total_amount=7000000,
            signal_type="GOLDEN_CROSS",
            trade_tier="TIER1",
            hybrid_score=82.5,
            timestamp=datetime.now(UTC),
        )
        msg = _format_trade_message(n)
        assert "*[매수 체결]*" in msg
        assert "삼성전자 (005930)" in msg
        assert "100주" in msg
        assert "70,000원" in msg
        assert "7,000,000원" in msg
        assert "GOLDEN_CROSS" in msg
        assert "TIER1" in msg
        assert "82.5" in msg

    def test_sell_message_format(self):
        from prime_jennie.services.telegram.app import _format_trade_message

        n = TradeNotification(
            trade_type="SELL",
            stock_code="005930",
            stock_name="삼성전자",
            quantity=100,
            price=80000,
            total_amount=8000000,
            sell_reason="PROFIT_TARGET",
            profit_pct=14.29,
            holding_days=5,
            timestamp=datetime.now(UTC),
        )
        msg = _format_trade_message(n)
        assert "*[매도 체결]*" in msg
        assert "삼성전자 (005930)" in msg
        assert "100주" in msg
        assert "80,000원" in msg
        assert "+14.29%" in msg
        assert "PROFIT_TARGET" in msg
        assert "5일" in msg

    def test_sell_message_negative_profit(self):
        from prime_jennie.services.telegram.app import _format_trade_message

        n = TradeNotification(
            trade_type="SELL",
            stock_code="005930",
            stock_name="삼성전자",
            quantity=50,
            price=65000,
            total_amount=3250000,
            sell_reason="STOP_LOSS",
            profit_pct=-5.00,
            timestamp=datetime.now(UTC),
        )
        msg = _format_trade_message(n)
        assert "-5.00%" in msg
        assert "STOP_LOSS" in msg

    def test_buy_message_without_optional_fields(self):
        from prime_jennie.services.telegram.app import _format_trade_message

        n = TradeNotification(
            trade_type="BUY",
            stock_code="005930",
            stock_name="삼성전자",
            quantity=10,
            price=70000,
            total_amount=700000,
            timestamp=datetime.now(UTC),
        )
        msg = _format_trade_message(n)
        assert "*[매수 체결]*" in msg
        assert "전략" not in msg  # no signal_type → no strategy line


# ─── 음소거 스킵 ─────────────────────────────────────────────


class TestMuteCheck:
    @patch("prime_jennie.services.telegram.app.get_redis")
    def test_muted_skips_notification(self, mock_get_redis):
        from prime_jennie.services.telegram.app import _is_muted

        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis
        # mute_until 은 미래 시각
        import time

        mock_redis.get.return_value = str(int(time.time()) + 600)
        assert _is_muted() is True

    @patch("prime_jennie.services.telegram.app.get_redis")
    def test_not_muted(self, mock_get_redis):
        from prime_jennie.services.telegram.app import _is_muted

        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis
        mock_redis.get.return_value = None
        assert _is_muted() is False

    @patch("prime_jennie.services.telegram.app.get_redis")
    def test_expired_mute(self, mock_get_redis):
        from prime_jennie.services.telegram.app import _is_muted

        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis
        # mute_until 이 과거
        import time

        mock_redis.get.return_value = str(int(time.time()) - 100)
        assert _is_muted() is False


# ─── Buyer _notify_buy fire-and-forget ───────────────────────


class TestBuyerNotifyBuy:
    @patch("prime_jennie.services.buyer.app._notifier")
    def test_notify_buy_publishes(self, mock_notifier):
        from prime_jennie.services.buyer.app import _notify_buy
        from prime_jennie.services.buyer.executor import ExecutionResult

        signal = MagicMock()
        signal.signal_type = "GOLDEN_CROSS"
        signal.trade_tier = "TIER1"
        signal.hybrid_score = 80.0

        result = ExecutionResult(
            "success",
            stock_code="005930",
            stock_name="삼성전자",
            quantity=100,
            price=70000,
        )

        _notify_buy(signal, result)
        mock_notifier.publish.assert_called_once()
        notification = mock_notifier.publish.call_args[0][0]
        assert notification.trade_type == "BUY"
        assert notification.stock_code == "005930"
        assert notification.total_amount == 7000000

    @patch("prime_jennie.services.buyer.app._notifier")
    def test_notify_buy_error_does_not_raise(self, mock_notifier):
        from prime_jennie.services.buyer.app import _notify_buy
        from prime_jennie.services.buyer.executor import ExecutionResult

        mock_notifier.publish.side_effect = Exception("Redis down")

        signal = MagicMock()
        signal.signal_type = "GOLDEN_CROSS"
        signal.trade_tier = "TIER1"
        signal.hybrid_score = 80.0

        result = ExecutionResult("success", stock_code="005930", stock_name="삼성전자", quantity=10, price=70000)

        # Should not raise
        _notify_buy(signal, result)

    def test_notify_buy_skips_when_notifier_none(self):
        import prime_jennie.services.buyer.app as buyer_app
        from prime_jennie.services.buyer.app import _notify_buy
        from prime_jennie.services.buyer.executor import ExecutionResult

        original = buyer_app._notifier
        try:
            buyer_app._notifier = None
            signal = MagicMock()
            result = ExecutionResult("success", stock_code="005930", stock_name="삼성전자", quantity=10, price=70000)
            # Should return without error
            _notify_buy(signal, result)
        finally:
            buyer_app._notifier = original


# ─── Seller _notify_sell fire-and-forget ─────────────────────


class TestSellerNotifySell:
    def test_notify_sell_publishes(self):
        from prime_jennie.services.seller.app import _notify_sell
        from prime_jennie.services.seller.executor import SellResult

        mock_notifier = MagicMock()

        order = MagicMock()
        order.sell_reason = "PROFIT_TARGET"
        order.holding_days = 5

        result = SellResult(
            "success",
            stock_code="005930",
            stock_name="삼성전자",
            quantity=100,
            price=80000,
            profit_pct=14.29,
        )

        _notify_sell(order, result, mock_notifier)
        mock_notifier.publish.assert_called_once()
        notification = mock_notifier.publish.call_args[0][0]
        assert notification.trade_type == "SELL"
        assert notification.profit_pct == 14.29
        assert notification.total_amount == 8000000

    def test_notify_sell_error_does_not_raise(self):
        from prime_jennie.services.seller.app import _notify_sell
        from prime_jennie.services.seller.executor import SellResult

        mock_notifier = MagicMock()
        mock_notifier.publish.side_effect = Exception("Redis down")

        order = MagicMock()
        order.sell_reason = "STOP_LOSS"
        order.holding_days = 3

        result = SellResult(
            "success", stock_code="005930", stock_name="삼성전자",
            quantity=50, price=65000, profit_pct=-5.0,
        )

        # Should not raise
        _notify_sell(order, result, mock_notifier)
