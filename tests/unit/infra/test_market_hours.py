"""MarketCalendar 단위 테스트."""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from prime_jennie.infra.market_hours import (
    MarketCalendar,
)

_KST = timezone(timedelta(hours=9))


def _kst(year, month, day, hour, minute):
    return datetime(year, month, day, hour, minute, tzinfo=_KST)


def _patch_now(dt):
    return patch("prime_jennie.infra.market_hours.datetime", wraps=datetime, **{"now.return_value": dt})


# ─── is_trading_day ──────────────────────────────────────────


class TestIsTradingDay:
    def test_weekend_saturday(self):
        cal = MarketCalendar()
        assert cal.is_trading_day(date(2026, 3, 7)) is False  # Saturday

    def test_weekend_sunday(self):
        cal = MarketCalendar()
        assert cal.is_trading_day(date(2026, 3, 8)) is False  # Sunday

    def test_weekday_no_checker(self):
        cal = MarketCalendar()
        assert cal.is_trading_day(date(2026, 3, 9)) is True  # Monday

    def test_holiday_with_checker(self):
        cal = MarketCalendar(trading_day_checker=lambda d: False)
        assert cal.is_trading_day(date(2026, 3, 9)) is False

    def test_trading_day_with_checker(self):
        cal = MarketCalendar(trading_day_checker=lambda d: True)
        assert cal.is_trading_day(date(2026, 3, 9)) is True

    def test_caches_result(self):
        call_count = 0

        def checker(d):
            nonlocal call_count
            call_count += 1
            return True

        cal = MarketCalendar(trading_day_checker=checker)
        cal.is_trading_day(date(2026, 3, 9))
        cal.is_trading_day(date(2026, 3, 9))
        assert call_count == 1  # 캐시 히트, 1회만 호출

    def test_checker_exception_assumes_trading_day(self):
        cal = MarketCalendar(trading_day_checker=lambda d: (_ for _ in ()).throw(RuntimeError("fail")))
        assert cal.is_trading_day(date(2026, 3, 9)) is True

    def test_weekend_skips_checker(self):
        call_count = 0

        def checker(d):
            nonlocal call_count
            call_count += 1
            return True

        cal = MarketCalendar(trading_day_checker=checker)
        cal.is_trading_day(date(2026, 3, 7))  # Saturday
        assert call_count == 0  # 주말은 API 미호출


# ─── is_market_open ──────────────────────────────────────────


class TestIsMarketOpen:
    def test_holiday(self):
        cal = MarketCalendar(trading_day_checker=lambda d: False)
        with _patch_now(_kst(2026, 3, 9, 10, 0)):
            is_open, session = cal.is_market_open()
        assert is_open is False
        assert session == "holiday"

    def test_weekend(self):
        cal = MarketCalendar()
        with _patch_now(_kst(2026, 3, 7, 10, 0)):  # Saturday
            is_open, session = cal.is_market_open()
        assert is_open is False
        assert session == "holiday"

    def test_pre_market(self):
        cal = MarketCalendar()
        with _patch_now(_kst(2026, 3, 9, 8, 30)):  # Monday 08:30
            is_open, session = cal.is_market_open()
        assert is_open is False
        assert session == "pre_market"

    def test_pre_opening(self):
        cal = MarketCalendar()
        with _patch_now(_kst(2026, 3, 9, 9, 0)):  # Monday 09:00
            is_open, session = cal.is_market_open()
        assert is_open is True
        assert session == "pre_opening"

    def test_regular(self):
        cal = MarketCalendar()
        with _patch_now(_kst(2026, 3, 9, 10, 0)):  # Monday 10:00
            is_open, session = cal.is_market_open()
        assert is_open is True
        assert session == "regular"

    def test_closing(self):
        cal = MarketCalendar()
        with _patch_now(_kst(2026, 3, 9, 15, 30)):  # Monday 15:30
            is_open, session = cal.is_market_open()
        assert is_open is True
        assert session == "closing"

    def test_after_hours(self):
        cal = MarketCalendar()
        with _patch_now(_kst(2026, 3, 9, 16, 0)):  # Monday 16:00
            is_open, session = cal.is_market_open()
        assert is_open is False
        assert session == "after_hours"


# ─── is_streaming_hours ──────────────────────────────────────


class TestIsStreamingHours:
    def test_streaming_start(self):
        cal = MarketCalendar()
        with _patch_now(_kst(2026, 3, 9, 8, 50)):  # Monday 08:50
            assert cal.is_streaming_hours() is True

    def test_streaming_end(self):
        cal = MarketCalendar()
        with _patch_now(_kst(2026, 3, 9, 15, 35)):  # Monday 15:35
            assert cal.is_streaming_hours() is True

    def test_before_streaming(self):
        cal = MarketCalendar()
        with _patch_now(_kst(2026, 3, 9, 8, 49)):  # Monday 08:49
            assert cal.is_streaming_hours() is False

    def test_after_streaming(self):
        cal = MarketCalendar()
        with _patch_now(_kst(2026, 3, 9, 15, 36)):  # Monday 15:36
            assert cal.is_streaming_hours() is False

    def test_holiday_not_streaming(self):
        cal = MarketCalendar(trading_day_checker=lambda d: False)
        with _patch_now(_kst(2026, 3, 9, 10, 0)):  # Monday but holiday
            assert cal.is_streaming_hours() is False

    def test_weekend_not_streaming(self):
        cal = MarketCalendar()
        with _patch_now(_kst(2026, 3, 7, 10, 0)):  # Saturday
            assert cal.is_streaming_hours() is False
