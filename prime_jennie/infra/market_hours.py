"""시장 운영 시간 유틸리티 — 거래일 + 장 시간 통합 체크.

Gateway에서는 KIS API(CTCA0903R)로 거래일 확인,
다른 서비스에서는 Gateway HTTP API를 통해 거래일 확인.
거래일 결과는 일 단위 캐시 (하루 1회만 API 호출).
"""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_KST = timezone(timedelta(hours=9))

# 장 시간 상수 (HHMM 정수)
MARKET_OPEN = 900  # 09:00
MARKET_CLOSE = 1530  # 15:30
STREAM_START = 850  # 08:50 (장 개시 10분 전)
STREAM_END = 1535  # 15:35 (장 마감 5분 후)


class MarketCalendar:
    """거래일 + 장 시간 통합 확인.

    Usage (Gateway — KIS API 직접):
        cal = MarketCalendar(lambda d: kis_api.is_trading_day(d))

    Usage (Scanner/Monitor — Gateway HTTP):
        cal = MarketCalendar(lambda d: _check_via_gateway(d))
    """

    def __init__(self, trading_day_checker: Callable | None = None):
        self._checker = trading_day_checker
        self._cache: dict[str, bool] = {}

    def is_trading_day(self, target=None) -> bool:
        """거래일 여부 (일 단위 캐시)."""
        now = datetime.now(_KST)
        d = target or now.date()
        key = d.isoformat()

        if key in self._cache:
            return self._cache[key]

        # 주말은 API 호출 없이 바로 False
        if d.weekday() >= 5:
            self._cache[key] = False
            return False

        if self._checker:
            try:
                result = self._checker(d)
                self._cache[key] = result
                if not result:
                    logger.info("Non-trading day (holiday): %s", key)
                return result
            except Exception:
                logger.warning("Trading day check failed for %s, assuming trading day", key)
                return True

        # 체커 없으면 평일=거래일 가정
        self._cache[key] = True
        return True

    def is_market_open(self) -> tuple[bool, str]:
        """장 운영 상태 반환 (is_open, session).

        Returns:
            (True, "regular") — 정규장 09:00~15:30
            (True, "pre_opening") — 동시호가 09:00 전
            (True, "closing") — 장 마감 동시호가 15:30~16:00
            (False, "holiday") — 공휴일/주말
            (False, "pre_market") — 장 개시 전
            (False, "after_hours") — 장 종료 후
        """
        now = datetime.now(_KST)

        if not self.is_trading_day(now.date()):
            return False, "holiday"

        t = now.hour * 100 + now.minute

        if t < MARKET_OPEN:
            return False, "pre_market"
        if t < 930:
            return True, "pre_opening"
        if t < MARKET_CLOSE:
            return True, "regular"
        if t < 1600:
            return True, "closing"
        return False, "after_hours"

    def is_streaming_hours(self) -> bool:
        """스트리밍 시간 여부 (08:50~15:35, 거래일만)."""
        now = datetime.now(_KST)

        if not self.is_trading_day(now.date()):
            return False

        t = now.hour * 100 + now.minute
        return STREAM_START <= t <= STREAM_END
