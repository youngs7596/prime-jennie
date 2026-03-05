"""KIS WebSocket Streamer 단위 테스트."""

import json
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from prime_jennie.services.gateway.streamer import (
    KISWebSocketStreamer,
    _BACKOFF_INITIAL,
    _BACKOFF_MAX,
    _STABLE_CONNECTION_SECS,
    _is_streaming_hours,
)


@pytest.fixture
def redis_mock():
    return MagicMock()


@pytest.fixture
def streamer(redis_mock):
    return KISWebSocketStreamer(
        redis_client=redis_mock,
        app_key="test_key",
        app_secret="test_secret",
        is_paper=True,
    )


# ─── _is_streaming_hours ────────────────────────────────────────


class TestIsStreamingHours:
    def test_weekday_in_hours(self):
        # 월요일 10:00 KST
        from datetime import datetime, timezone, timedelta

        kst = timezone(timedelta(hours=9))
        dt = datetime(2026, 3, 2, 10, 0, tzinfo=kst)  # Monday
        with patch("prime_jennie.services.gateway.streamer.datetime") as mock_dt:
            mock_dt.now.return_value = dt
            assert _is_streaming_hours() is True

    def test_weekday_before_hours(self):
        from datetime import datetime, timezone, timedelta

        kst = timezone(timedelta(hours=9))
        dt = datetime(2026, 3, 2, 7, 0, tzinfo=kst)  # Monday 07:00
        with patch("prime_jennie.services.gateway.streamer.datetime") as mock_dt:
            mock_dt.now.return_value = dt
            assert _is_streaming_hours() is False

    def test_weekday_after_hours(self):
        from datetime import datetime, timezone, timedelta

        kst = timezone(timedelta(hours=9))
        dt = datetime(2026, 3, 2, 16, 0, tzinfo=kst)  # Monday 16:00
        with patch("prime_jennie.services.gateway.streamer.datetime") as mock_dt:
            mock_dt.now.return_value = dt
            assert _is_streaming_hours() is False

    def test_weekend(self):
        from datetime import datetime, timezone, timedelta

        kst = timezone(timedelta(hours=9))
        dt = datetime(2026, 3, 7, 10, 0, tzinfo=kst)  # Saturday
        with patch("prime_jennie.services.gateway.streamer.datetime") as mock_dt:
            mock_dt.now.return_value = dt
            assert _is_streaming_hours() is False

    def test_edge_start(self):
        from datetime import datetime, timezone, timedelta

        kst = timezone(timedelta(hours=9))
        dt = datetime(2026, 3, 2, 8, 50, tzinfo=kst)  # Monday 08:50
        with patch("prime_jennie.services.gateway.streamer.datetime") as mock_dt:
            mock_dt.now.return_value = dt
            assert _is_streaming_hours() is True

    def test_edge_end(self):
        from datetime import datetime, timezone, timedelta

        kst = timezone(timedelta(hours=9))
        dt = datetime(2026, 3, 2, 15, 35, tzinfo=kst)  # Monday 15:35
        with patch("prime_jennie.services.gateway.streamer.datetime") as mock_dt:
            mock_dt.now.return_value = dt
            assert _is_streaming_hours() is True

    def test_just_after_end(self):
        from datetime import datetime, timezone, timedelta

        kst = timezone(timedelta(hours=9))
        dt = datetime(2026, 3, 2, 15, 36, tzinfo=kst)  # Monday 15:36
        with patch("prime_jennie.services.gateway.streamer.datetime") as mock_dt:
            mock_dt.now.return_value = dt
            assert _is_streaming_hours() is False


# ─── Hot Subscribe (add_subscriptions) ──────────────────────────


class TestAddSubscriptions:
    def test_adds_new_codes(self, streamer):
        added = streamer.add_subscriptions(["005930", "000660"])
        assert added == ["005930", "000660"]
        assert streamer.subscription_count == 2

    def test_ignores_duplicates(self, streamer):
        streamer.add_subscriptions(["005930"])
        added = streamer.add_subscriptions(["005930", "000660"])
        assert added == ["000660"]
        assert streamer.subscription_count == 2

    def test_hot_subscribe_sends_to_ws(self, streamer):
        """실행 중일 때 add_subscriptions → ws.send 호출 (재시작 아님)."""
        ws_mock = MagicMock()
        streamer._ws = ws_mock
        streamer._is_running = True
        streamer._approval_key = "test_key"

        added = streamer.add_subscriptions(["005930"])
        assert added == ["005930"]

        # ws.send가 호출되었는지 확인
        assert ws_mock.send.called
        sent_msg = json.loads(ws_mock.send.call_args[0][0])
        assert sent_msg["header"]["tr_type"] == "1"
        assert sent_msg["body"]["input"]["tr_key"] == "005930"

    def test_no_send_when_not_running(self, streamer):
        """실행 중이 아닐 때 ws.send 호출 안 함."""
        ws_mock = MagicMock()
        streamer._ws = ws_mock
        streamer._is_running = False

        streamer.add_subscriptions(["005930"])
        ws_mock.send.assert_not_called()


# ─── remove_subscriptions ───────────────────────────────────────


class TestRemoveSubscriptions:
    def test_removes_existing_codes(self, streamer):
        streamer.add_subscriptions(["005930", "000660", "035420"])
        removed = streamer.remove_subscriptions(["005930", "000660"])
        assert removed == ["005930", "000660"]
        assert streamer.subscription_count == 1
        assert "035420" in streamer.subscribed_codes

    def test_ignores_nonexistent_codes(self, streamer):
        streamer.add_subscriptions(["005930"])
        removed = streamer.remove_subscriptions(["999999"])
        assert removed == []
        assert streamer.subscription_count == 1

    def test_sends_unsubscribe_message(self, streamer):
        """실행 중일 때 tr_type='2' 전송."""
        streamer.add_subscriptions(["005930"])
        ws_mock = MagicMock()
        streamer._ws = ws_mock
        streamer._is_running = True
        streamer._approval_key = "test_key"

        streamer.remove_subscriptions(["005930"])

        assert ws_mock.send.called
        sent_msg = json.loads(ws_mock.send.call_args[0][0])
        assert sent_msg["header"]["tr_type"] == "2"
        assert sent_msg["body"]["input"]["tr_key"] == "005930"


# ─── _send_subscribe ────────────────────────────────────────────


class TestSendSubscribe:
    def test_subscribe_message_format(self, streamer):
        ws_mock = MagicMock()
        streamer._approval_key = "approval_123"
        streamer._is_running = True

        streamer._send_subscribe(ws_mock, ["005930"], tr_type="1")

        ws_mock.send.assert_called_once()
        msg = json.loads(ws_mock.send.call_args[0][0])
        assert msg["header"]["approval_key"] == "approval_123"
        assert msg["header"]["tr_type"] == "1"
        assert msg["body"]["input"]["tr_id"] == "H0STCNT0"
        assert msg["body"]["input"]["tr_key"] == "005930"

    def test_unsubscribe_message_format(self, streamer):
        ws_mock = MagicMock()
        streamer._approval_key = "approval_123"
        streamer._is_running = True

        streamer._send_subscribe(ws_mock, ["005930"], tr_type="2")

        msg = json.loads(ws_mock.send.call_args[0][0])
        assert msg["header"]["tr_type"] == "2"

    def test_stops_on_send_failure(self, streamer):
        ws_mock = MagicMock()
        ws_mock.send.side_effect = Exception("connection lost")
        streamer._approval_key = "key"
        streamer._is_running = True

        # Should not raise
        streamer._send_subscribe(ws_mock, ["005930", "000660"], tr_type="1")
        assert ws_mock.send.call_count == 1  # 첫 번째 실패 시 중단

    def test_multiple_codes(self, streamer):
        ws_mock = MagicMock()
        streamer._approval_key = "key"
        streamer._is_running = True

        streamer._send_subscribe(ws_mock, ["005930", "000660", "035420"], tr_type="1")
        assert ws_mock.send.call_count == 3


# ─── Thread Safety ──────────────────────────────────────────────


class TestThreadSafety:
    def test_concurrent_add_subscriptions(self, streamer):
        """여러 스레드에서 동시 구독 추가."""
        results = []

        def add_codes(codes):
            r = streamer.add_subscriptions(codes)
            results.append(r)

        threads = [
            threading.Thread(target=add_codes, args=(["005930"],)),
            threading.Thread(target=add_codes, args=(["000660"],)),
            threading.Thread(target=add_codes, args=(["035420"],)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert streamer.subscription_count == 3

    def test_concurrent_add_remove(self, streamer):
        """동시 추가/제거."""
        streamer.add_subscriptions(["005930", "000660"])

        def add():
            streamer.add_subscriptions(["035420"])

        def remove():
            streamer.remove_subscriptions(["000660"])

        t1 = threading.Thread(target=add)
        t2 = threading.Thread(target=remove)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        codes = streamer.subscribed_codes
        assert "035420" in codes
        assert "005930" in codes
        assert "000660" not in codes


# ─── Exponential Backoff ────────────────────────────────────────


class TestExponentialBackoff:
    def test_backoff_constants(self):
        assert _BACKOFF_INITIAL == 60
        assert _BACKOFF_MAX == 600
        assert _STABLE_CONNECTION_SECS == 30

    @patch("prime_jennie.services.gateway.streamer._is_streaming_hours", return_value=True)
    def test_backoff_doubles_on_quick_disconnect(self, mock_hours, streamer):
        """즉시 끊기면 backoff 2배 증가."""
        call_count = 0
        sleep_values = []

        def mock_run_forever(self_ws):
            nonlocal call_count
            call_count += 1

        original_sleep = time.sleep

        def mock_sleep(secs):
            sleep_values.append(secs)
            if len(sleep_values) >= 3:
                streamer._is_running = False

        with (
            patch("websocket.WebSocketApp") as mock_ws_cls,
            patch("time.sleep", side_effect=mock_sleep),
        ):
            mock_ws_instance = MagicMock()
            mock_ws_instance.run_forever = lambda: None
            mock_ws_cls.return_value = mock_ws_instance

            streamer._subscription_codes = {"005930"}
            streamer._is_running = True
            streamer._approval_key = "key"
            streamer._ws_loop("key")

        # backoff: 120 (60*2), 240 (120*2), 480 (240*2)
        assert len(sleep_values) >= 2
        assert sleep_values[0] == _BACKOFF_INITIAL * 2  # 첫 번째: connected_at=0 → double
        assert sleep_values[1] == _BACKOFF_INITIAL * 4

    @patch("prime_jennie.services.gateway.streamer._is_streaming_hours", return_value=True)
    def test_backoff_resets_after_stable_connection(self, mock_hours, streamer):
        """안정 연결(30초 이상) 후 끊기면 backoff 리셋."""
        sleep_values = []

        def mock_sleep(secs):
            sleep_values.append(secs)
            # Only stop after a backoff sleep (not the 0.05s inter-subscribe delay)
            if secs >= 1:
                streamer._is_running = False

        time_base = [1000.0]

        def mock_time():
            return time_base[0]

        captured_on_open = [None]

        def fake_ws_app(url, on_open=None, **kwargs):
            captured_on_open[0] = on_open
            mock_ws = MagicMock()

            def fake_run_forever():
                # Trigger on_open so connected_at gets set
                if captured_on_open[0]:
                    captured_on_open[0](mock_ws)
                # Simulate stable connection (advance time by 60 seconds)
                time_base[0] += 60

            mock_ws.run_forever = fake_run_forever
            return mock_ws

        with (
            patch("websocket.WebSocketApp", side_effect=fake_ws_app),
            patch("time.sleep", side_effect=mock_sleep),
            patch("time.time", side_effect=mock_time),
        ):
            streamer._subscription_codes = {"005930"}
            streamer._is_running = True
            streamer._approval_key = "key"
            streamer._ws_loop("key")

        # Filter out inter-subscribe 0.05s delays, only check backoff sleeps
        backoff_sleeps = [s for s in sleep_values if s >= 1]
        # 60초 유지 → backoff 리셋 → _BACKOFF_INITIAL
        assert backoff_sleeps[0] == _BACKOFF_INITIAL


# ─── ws_loop: 장외 시간 대기 ─────────────────────────────────────


class TestWsLoopStreamingHours:
    @patch("prime_jennie.services.gateway.streamer._is_streaming_hours")
    def test_skips_connection_outside_hours(self, mock_hours, streamer):
        """장외 시간에는 연결 시도 안 함."""
        call_count = [0]

        def hours_side_effect():
            call_count[0] += 1
            if call_count[0] <= 2:
                return False  # 처음 2번은 장외
            streamer._is_running = False
            return False

        mock_hours.side_effect = hours_side_effect

        sleep_calls = []

        def mock_sleep(secs):
            sleep_calls.append(secs)

        with patch("time.sleep", side_effect=mock_sleep):
            streamer._is_running = True
            streamer._subscription_codes = {"005930"}
            streamer._ws_loop("key")

        # 장외 시간에 60초 sleep
        assert all(s == 60 for s in sleep_calls)


# ─── _handle_message ────────────────────────────────────────────


class TestHandleMessage:
    def test_pingpong_echoed(self, streamer):
        ws = MagicMock()
        msg = json.dumps({"header": {"tr_id": "PINGPONG"}})
        streamer._handle_message(ws, msg)
        ws.send.assert_called_once_with(msg)

    def test_tick_data_published(self, streamer, redis_mock):
        ws = MagicMock()
        # 0|H0STCNT0|001|005930^20260305^72100^...^72500^...^...^...^...^...^15000000^...
        fields = ["005930", "20260305", "72100", "100", "200", "72500"]
        fields += ["0"] * 5  # pad to index 10
        fields.append("15000000")  # volume at index 10 (but actually index 11 here)
        # Correct: fields[0]=code, fields[2]=price, fields[5]=high, fields[10]=volume
        fields_str = "^".join(["005930", "X", "72100", "X", "X", "72500", "X", "X", "X", "X", "15000000"])
        msg = f"0|H0STCNT0|001|{fields_str}"

        streamer._handle_message(ws, msg)
        redis_mock.xadd.assert_called_once()
        call_args = redis_mock.xadd.call_args
        assert call_args[0][1]["code"] == "005930"
        assert call_args[0][1]["price"] == "72100"
        assert call_args[0][1]["high"] == "72500"
        assert call_args[0][1]["vol"] == "15000000"

    def test_empty_message_ignored(self, streamer):
        ws = MagicMock()
        streamer._handle_message(ws, "")
        ws.send.assert_not_called()

    def test_non_tick_message_ignored(self, streamer, redis_mock):
        ws = MagicMock()
        streamer._handle_message(ws, "2|some|data|here")
        redis_mock.xadd.assert_not_called()


# ─── get_status ─────────────────────────────────────────────────


class TestGetStatus:
    def test_status_format(self, streamer):
        streamer.add_subscriptions(["005930", "000660"])
        status = streamer.get_status()
        assert status["is_running"] is False
        assert status["subscription_count"] == 2
        assert status["codes"] == ["000660", "005930"]


# ─── _restart 제거 확인 ──────────────────────────────────────────


class TestNoRestart:
    def test_no_restart_method(self, streamer):
        """_restart 메서드가 제거되었는지 확인."""
        assert not hasattr(streamer, "_restart")
