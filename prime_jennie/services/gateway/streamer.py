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
        return len(self._subscription_codes)

    @property
    def subscribed_codes(self) -> list[str]:
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
        """구독 종목 추가. 새로 추가된 코드 목록 반환."""
        new_codes = [c for c in codes if c not in self._subscription_codes]
        self._subscription_codes.update(new_codes)

        # 이미 실행 중이면 재시작 (새 종목 구독)
        if self._is_running and new_codes:
            self._restart()

        return new_codes

    def start(self, base_url: str) -> None:
        """WebSocket 연결 시작 (백그라운드 스레드)."""
        if self._is_running:
            logger.warning("Streamer already running")
            return

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

    def _restart(self) -> None:
        """재시작 (새 종목 추가 시)."""
        self.stop()
        time.sleep(0.5)
        # approval key는 캐시에서 재사용
        if self._approval_key:
            self._is_running = True
            self._ws_thread = threading.Thread(target=self._ws_loop, args=(self._approval_key,), daemon=True)
            self._ws_thread.start()

    def _ws_loop(self, approval_key: str) -> None:
        """WebSocket 메인 루프 (재연결 시 approval_key 갱신)."""
        try:
            import websocket
        except ImportError:
            logger.error("websocket-client not installed")
            self._is_running = False
            return

        while self._is_running:
            current_key = approval_key

            def on_open(ws, _key=current_key):
                logger.info("KIS WebSocket connected")
                threading.Thread(target=self._send_subscriptions, args=(ws, _key), daemon=True).start()

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

            logger.info("Reconnecting in 60s...")
            time.sleep(60)

            # 재연결 시 approval_key 갱신
            if self._base_url:
                try:
                    self._approval_key_expires = 0.0  # 캐시 무효화
                    approval_key = self.get_approval_key(self._base_url)
                    logger.info("Approval key refreshed for reconnect")
                except Exception as e:
                    logger.warning("Failed to refresh approval key, reusing old: %s", e)

    def _send_subscriptions(self, ws, approval_key: str) -> None:
        """종목별 구독 요청."""
        for code in list(self._subscription_codes):
            if not self._is_running:
                break
            msg = json.dumps(
                {
                    "header": {
                        "approval_key": approval_key,
                        "custtype": "P",
                        "tr_type": "1",
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

        logger.info("Subscribed to %d codes", len(self._subscription_codes))

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
        return {
            "is_running": self._is_running,
            "subscription_count": len(self._subscription_codes),
            "codes": sorted(self._subscription_codes),
        }
