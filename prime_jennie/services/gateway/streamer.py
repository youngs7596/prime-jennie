"""KIS WebSocket Streamer — 실시간 체결가 수신 → Redis Stream 발행.

KIS API WebSocket (H0STCNT0) 연결, 종목별 체결가 수신 후
Redis Stream kis:prices 에 발행. buy-scanner 등 다운스트림이 소비.

Architecture:
  KIS WebSocket (ws://ops.koreainvestment.com:21000)
    → on_message → parse tick → Redis XADD kis:prices
"""

import contextlib
import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

import redis

logger = logging.getLogger(__name__)

# KIS WebSocket URLs
WS_URL_REAL = "ws://ops.koreainvestment.com:21000"
WS_URL_PAPER = "ws://ops.koreainvestment.com:31000"

# Redis Stream
PRICE_STREAM = "kis:prices"
PRICE_STREAM_MAXLEN = 10_000

# KIS TR ID for real-time stock execution price
TR_ID_STOCK_EXEC = "H0STCNT0"

# KST timezone
_KST = timezone(timedelta(hours=9))

# Backoff constants
_BACKOFF_INITIAL = 60
_BACKOFF_MAX = 600
_STABLE_CONNECTION_SECS = 30


def _is_streaming_hours() -> bool:
    """장 시간 체크 (08:50~15:35 KST, 평일만)."""
    now = datetime.now(_KST)
    if now.weekday() >= 5:  # 토/일
        return False
    t = now.hour * 100 + now.minute
    return 850 <= t <= 1535


class KISWebSocketStreamer:
    """KIS WebSocket → Redis Stream 스트리머.

    Gateway 내부에서 싱글턴으로 관리. 여러 서비스의 구독 요청을 통합.
    """

    def __init__(
        self,
        redis_client: redis.Redis,
        app_key: str,
        app_secret: str,
        is_paper: bool = False,
    ):
        self._redis = redis_client
        self._app_key = app_key
        self._app_secret = app_secret
        self._ws_url = WS_URL_PAPER if is_paper else WS_URL_REAL
        self._subscription_codes: set[str] = set()
        self._ws = None
        self._ws_thread: threading.Thread | None = None
        self._is_running = False
        self._base_url: str = ""
        self._approval_key: str | None = None
        self._approval_key_expires: float = 0.0
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

    def get_approval_key(self, base_url: str) -> str:
        """WebSocket approval key 발급 (캐싱 30초)."""
        if self._approval_key and time.time() < self._approval_key_expires:
            return self._approval_key

        import httpx

        resp = httpx.post(
            f"{base_url}/oauth2/Approval",
            json={
                "grant_type": "client_credentials",
                "appkey": self._app_key,
                "secretkey": self._app_secret,
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()

        self._approval_key = data.get("approval_key", "")
        self._approval_key_expires = time.time() + 30
        return self._approval_key

    def add_subscriptions(self, codes: list[str]) -> list[str]:
        """구독 종목 추가. 새로 추가된 코드 목록 반환. 재시작 없이 hot subscribe."""
        with self._lock:
            new_codes = [c for c in codes if c not in self._subscription_codes]
            self._subscription_codes.update(new_codes)

        # 이미 실행 중이면 기존 WS에서 구독 메시지만 전송 (재시작 없음)
        if self._is_running and new_codes and self._ws and self._approval_key:
            try:
                self._send_subscribe(self._ws, new_codes, tr_type="1")
            except Exception as e:
                logger.warning("Hot subscribe failed (will retry on reconnect): %s", e)

        return new_codes

    def remove_subscriptions(self, codes: list[str]) -> list[str]:
        """구독 종목 해제. 해제된 코드 목록 반환."""
        with self._lock:
            removed = [c for c in codes if c in self._subscription_codes]
            self._subscription_codes -= set(removed)

        if self._is_running and removed and self._ws and self._approval_key:
            try:
                self._send_subscribe(self._ws, removed, tr_type="2")
            except Exception as e:
                logger.warning("Unsubscribe send failed: %s", e)

        return removed

    def start(self, base_url: str) -> None:
        """WebSocket 연결 시작 (백그라운드 스레드)."""
        if self._is_running:
            logger.warning("Streamer already running")
            return

        with self._lock:
            if not self._subscription_codes:
                logger.warning("No codes to subscribe")
                return

        self._base_url = base_url

        try:
            approval_key = self.get_approval_key(base_url)
        except Exception as e:
            logger.error("Failed to get approval key: %s", e)
            return

        self._is_running = True
        self._ws_thread = threading.Thread(target=self._ws_loop, args=(approval_key,), daemon=True)
        self._ws_thread.start()
        logger.info("Streamer started: %d codes, url=%s", len(self._subscription_codes), self._ws_url)

    def stop(self) -> None:
        """WebSocket 연결 종료."""
        self._is_running = False
        if self._ws:
            with contextlib.suppress(Exception):
                self._ws.close()
        self._ws = None
        logger.info("Streamer stopped")

    def _send_subscribe(self, ws, codes: list[str], tr_type: str = "1") -> None:
        """종목별 구독/해제 요청 전송. tr_type '1'=구독, '2'=해제."""
        for code in codes:
            if not self._is_running:
                break
            msg = json.dumps(
                {
                    "header": {
                        "approval_key": self._approval_key,
                        "custtype": "P",
                        "tr_type": tr_type,
                        "content-type": "utf-8",
                    },
                    "body": {
                        "input": {
                            "tr_id": TR_ID_STOCK_EXEC,
                            "tr_key": code,
                        }
                    },
                }
            )
            try:
                ws.send(msg)
            except Exception:
                break
            time.sleep(0.05)  # 50ms 간격

    def _ws_loop(self, approval_key: str) -> None:
        """WebSocket 메인 루프 (장외 시간 대기 + exponential backoff)."""
        try:
            import websocket
        except ImportError:
            logger.error("websocket-client not installed")
            self._is_running = False
            return

        backoff = _BACKOFF_INITIAL

        while self._is_running:
            # 장외 시간이면 60초 sleep 후 재확인 (backoff도 리셋)
            if not _is_streaming_hours():
                logger.debug("Outside streaming hours, waiting 60s...")
                time.sleep(60)
                backoff = _BACKOFF_INITIAL
                continue

            current_key = approval_key
            connected_at = 0.0

            def on_open(ws, _key=current_key):
                nonlocal connected_at
                connected_at = time.time()
                logger.info("KIS WebSocket connected")
                with self._lock:
                    codes = list(self._subscription_codes)
                self._send_subscribe(ws, codes, tr_type="1")
                logger.info("Subscribed to %d codes", len(codes))

            def on_message(ws, message):
                self._handle_message(ws, message)

            def on_error(ws, error):
                logger.warning("KIS WebSocket error: %s", error)

            def on_close(ws, close_status_code, close_msg):
                logger.info("KIS WebSocket closed: %s %s", close_status_code, close_msg)

            self._ws = websocket.WebSocketApp(
                self._ws_url,
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
            )
            self._ws.run_forever()

            if not self._is_running:
                break

            # Exponential backoff: 안정 연결(30초 이상) 후 끊기면 리셋
            elapsed = time.time() - connected_at if connected_at else 0
            backoff = _BACKOFF_INITIAL if elapsed >= _STABLE_CONNECTION_SECS else min(backoff * 2, _BACKOFF_MAX)

            logger.info("Reconnecting in %ds (connection lasted %.0fs)...", backoff, elapsed)
            time.sleep(backoff)

            # 재연결 시 approval_key 갱신
            if self._base_url:
                try:
                    self._approval_key_expires = 0.0  # 캐시 무효화
                    approval_key = self.get_approval_key(self._base_url)
                    logger.info("Approval key refreshed for reconnect")
                except Exception as e:
                    logger.warning("Failed to refresh approval key, reusing old: %s", e)

    def _handle_message(self, ws, message: str) -> None:
        """WebSocket 메시지 파싱 → Redis XADD.

        JSON 메시지(PINGPONG, 구독 응답)와 tick 데이터('0'|'1' 시작)를 구분 처리.
        """
        if not message:
            return

        # JSON 메시지: PINGPONG echo 또는 구독 응답
        if message.startswith("{"):
            try:
                data = json.loads(message)
                tr_id = data.get("header", {}).get("tr_id", "")
                if tr_id == "PINGPONG":
                    with contextlib.suppress(Exception):
                        ws.send(message)
                    logger.debug("PINGPONG echoed")
                else:
                    logger.debug("KIS JSON msg: %s", message[:200])
            except json.JSONDecodeError:
                logger.debug("Non-JSON message starting with '{': %s", message[:100])
            return

        if message[0] not in ("0", "1"):
            return

        try:
            parts = message.split("|")
            if len(parts) < 4:
                return

            data_part = parts[3]
            fields = data_part.split("^")
            if len(fields) < 6:
                return

            stock_code = fields[0]
            price = fields[2]
            high = fields[5]
            volume = fields[10] if len(fields) > 10 else "0"

            self._redis.xadd(
                PRICE_STREAM,
                {"code": stock_code, "price": price, "high": high, "vol": volume},
                maxlen=PRICE_STREAM_MAXLEN,
                approximate=True,
            )
        except (IndexError, ValueError) as e:
            logger.debug("Tick parse error: %s", e)

    def get_status(self) -> dict:
        """상태 조회."""
        with self._lock:
            codes = sorted(self._subscription_codes)
        return {
            "is_running": self._is_running,
            "subscription_count": len(codes),
            "codes": codes,
        }
