"""TypedStreamPublisher/Consumer — Redis Streams + Pydantic 직렬화.

Design:
  - Publisher: Pydantic 모델 → JSON → XADD
  - Consumer: XREADGROUP → JSON → Pydantic 모델 → handler(model)
  - Consumer group: at-most-once delivery (ACK before handler)
  - Pending recovery: 자동으로 미완료 메시지 재처리

Usage:
    publisher = TypedStreamPublisher(redis, "buy_signals", BuySignal)
    publisher.publish(signal)

    consumer = TypedStreamConsumer(
        redis, "buy_signals", "executor_group", "executor_1",
        BuySignal, handler=process_signal,
    )
    consumer.run()  # blocking
"""

import logging
import time
from collections.abc import Callable
from typing import Generic, TypeVar

import redis
from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class TypedStreamPublisher(Generic[T]):
    """Pydantic 모델을 Redis Stream에 발행."""

    def __init__(
        self,
        client: redis.Redis,
        stream: str,
        model_class: type[T],
        maxlen: int = 10000,
    ):
        self._client = client
        self._stream = stream
        self._model_class = model_class
        self._maxlen = maxlen

    def publish(self, message: T) -> str:
        """메시지 발행. 반환값: message ID."""
        data = {"payload": message.model_dump_json()}
        msg_id = self._client.xadd(self._stream, data, maxlen=self._maxlen, approximate=True)
        logger.debug(
            "Published to %s: id=%s type=%s",
            self._stream,
            msg_id,
            self._model_class.__name__,
        )
        return msg_id


class TypedStreamConsumer(Generic[T]):
    """Redis Stream 소비자 — Pydantic 모델 역직렬화 + handler 호출."""

    def __init__(
        self,
        client: redis.Redis,
        stream: str,
        group: str,
        consumer: str,
        model_class: type[T],
        handler: Callable[[T], None],
        batch_size: int = 1,
        block_ms: int = 5000,
    ):
        self._client = client
        self._stream = stream
        self._group = group
        self._consumer = consumer
        self._model_class = model_class
        self._handler = handler
        self._batch_size = batch_size
        self._block_ms = block_ms
        self._running = False
        self._ensure_group()

    def _ensure_group(self) -> None:
        """Consumer group 생성 (이미 존재하면 무시, Redis 준비 대기 최대 30초)."""
        for attempt in range(30):
            try:
                self._client.xgroup_create(self._stream, self._group, id="0", mkstream=True)
                return
            except redis.exceptions.ResponseError as e:
                if "BUSYGROUP" in str(e):
                    return
                raise
            except (ConnectionError, redis.exceptions.BusyLoadingError):
                logger.warning(
                    "Redis not ready for group '%s' (attempt %d/30), retrying...",
                    self._group,
                    attempt + 1,
                )
                time.sleep(1)
        logger.error("Redis not ready after 30s, group '%s' creation skipped", self._group)

    def run(self) -> None:
        """메시지 소비 루프 (blocking). KeyboardInterrupt로 종료."""
        self._running = True
        logger.info(
            "Consumer started: stream=%s group=%s consumer=%s",
            self._stream,
            self._group,
            self._consumer,
        )

        # Pending 복구
        self._recover_pending()

        while self._running:
            try:
                messages = self._client.xreadgroup(
                    self._group,
                    self._consumer,
                    {self._stream: ">"},
                    count=self._batch_size,
                    block=self._block_ms,
                )
                if not messages:
                    continue

                for _stream_name, entries in messages:
                    for msg_id, data in entries:
                        self._process_message(msg_id, data)

            except KeyboardInterrupt:
                logger.info("Consumer shutting down...")
                self._running = False
            except redis.exceptions.ConnectionError:
                logger.error("Redis connection lost, retrying in 5s...")
                time.sleep(5)
            except Exception:
                logger.exception("Consumer error")
                time.sleep(1)

    def stop(self) -> None:
        """소비 루프 중지."""
        self._running = False

    def _process_message(self, msg_id: str, data: dict) -> None:
        """ACK → 역직렬화 → handler (at-most-once)."""
        # ACK first (at-most-once delivery)
        self._client.xack(self._stream, self._group, msg_id)

        payload = data.get("payload")
        if not payload:
            logger.warning("Empty payload: stream=%s id=%s", self._stream, msg_id)
            return

        try:
            model = self._model_class.model_validate_json(payload)
            self._handler(model)
        except Exception:
            logger.exception("Handler failed: stream=%s id=%s", self._stream, msg_id)

    def _recover_pending(self) -> None:
        """미완료 pending 메시지 복구."""
        try:
            pending = self._client.xpending_range(self._stream, self._group, "-", "+", count=100)
            if not pending:
                return

            logger.info(
                "Recovering %d pending messages from %s",
                len(pending),
                self._stream,
            )

            msg_ids = [p["message_id"] for p in pending]
            claimed = self._client.xclaim(
                self._stream,
                self._group,
                self._consumer,
                min_idle_time=60000,  # 1분 이상 대기
                message_ids=msg_ids,
            )
            for msg_id, data in claimed:
                self._process_message(msg_id, data)

        except Exception:
            logger.exception("Pending recovery failed")
