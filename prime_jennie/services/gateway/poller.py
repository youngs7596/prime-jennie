"""KIS REST Poller — WebSocket 차단 시 REST API 폴링 백업.

KIS WebSocket 접속이 불가능할 때 REST API(get_snapshot)를 주기적으로 호출하여
Redis Stream kis:prices 에 발행. downstream(scanner, monitor)은 동일 스트림 소비.

3초 간격, 최대 45종목, 초당 15건 → KIS 제한(20건) 대비 75%.
"""

import logging
import threading
import time

from .streamer import PRICE_STREAM, PRICE_STREAM_MAXLEN, _is_streaming_hours

logger = logging.getLogger(__name__)

# 장외 시간 대기 간격
_OFF_HOURS_SLEEP = 60


class KISRestPoller:
    """KIS REST API 폴링 → Redis Stream 스트리머.

    KISWebSocketStreamer와 동일한 인터페이스(duck typing).
    """

    def __init__(self, redis_client, kis_api, polling_interval: float = 3.0):
        self._redis = redis_client
        self._kis_api = kis_api
        self._polling_interval = polling_interval
        self._subscription_codes: set[str] = set()
        self._is_running = False
        self._poll_thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def subscription_count(self) -> int:
        with self._lock:
            return len(self._subscription_codes)

    @property
    def subscribed_codes(self) -> list[str]:
        with self._lock:
            return sorted(self._subscription_codes)

    def add_subscriptions(self, codes: list[str]) -> list[str]:
        """구독 종목 추가. 새로 추가된 코드 목록 반환."""
        with self._lock:
            new_codes = [c for c in codes if c not in self._subscription_codes]
            self._subscription_codes.update(new_codes)
        return new_codes

    def remove_subscriptions(self, codes: list[str]) -> list[str]:
        """구독 종목 해제. 해제된 코드 목록 반환."""
        with self._lock:
            removed = [c for c in codes if c in self._subscription_codes]
            self._subscription_codes -= set(removed)
        return removed

    def start(self, base_url: str | None = None) -> None:
        """폴링 시작 (백그라운드 스레드). base_url은 인터페이스 호환용 (무시)."""
        if self._is_running:
            logger.warning("Poller already running")
            return

        with self._lock:
            if not self._subscription_codes:
                logger.warning("No codes to poll")
                return

        self._is_running = True
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()
        logger.info(
            "REST Poller started: %d codes, interval=%.1fs",
            len(self._subscription_codes),
            self._polling_interval,
        )

    def stop(self) -> None:
        """폴링 종료."""
        self._is_running = False
        logger.info("REST Poller stopped")

    def get_status(self) -> dict:
        """상태 조회."""
        with self._lock:
            codes = sorted(self._subscription_codes)
        return {
            "is_running": self._is_running,
            "subscription_count": len(codes),
            "codes": codes,
            "mode": "polling",
        }

    def _poll_loop(self) -> None:
        """폴링 메인 루프 — 3초 주기로 전체 종목 순회."""
        while self._is_running:
            if not _is_streaming_hours():
                time.sleep(_OFF_HOURS_SLEEP)
                continue

            cycle_start = time.monotonic()

            with self._lock:
                codes = list(self._subscription_codes)

            for code in codes:
                if not self._is_running:
                    return
                try:
                    snapshot = self._kis_api.get_snapshot(code)
                    self._redis.xadd(
                        PRICE_STREAM,
                        {
                            "code": snapshot.stock_code,
                            "price": str(snapshot.price),
                            "high": str(snapshot.high_price),
                            "vol": str(snapshot.volume),
                        },
                        maxlen=PRICE_STREAM_MAXLEN,
                        approximate=True,
                    )
                except Exception:
                    logger.warning("Snapshot failed for %s, skipping", code, exc_info=True)

            # 남은 시간만큼 sleep (목표: polling_interval 주기)
            elapsed = time.monotonic() - cycle_start
            remaining = self._polling_interval - elapsed
            if remaining > 0 and self._is_running:
                time.sleep(remaining)
