"""TypedCache — Pydantic 모델 기반 Redis 캐시.

Usage:
    from prime_jennie.domain import HotWatchlist
    cache = TypedCache(redis_client, "watchlist:active", HotWatchlist, ttl=86400)
    cache.set(watchlist)
    wl = cache.get()  # -> Optional[HotWatchlist]
"""

import logging
from typing import Generic, TypeVar

import redis
from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class TypedCache(Generic[T]):
    """Pydantic 모델 직렬화/역직렬화를 보장하는 Redis 캐시."""

    def __init__(
        self,
        client: redis.Redis,
        key: str,
        model_class: type[T],
        ttl: int | None = None,
    ):
        self._client = client
        self._key = key
        self._model_class = model_class
        self._ttl = ttl

    def get(self) -> T | None:
        """캐시에서 읽기. 없거나 파싱 실패 시 None."""
        raw = self._client.get(self._key)
        if raw is None:
            return None
        try:
            return self._model_class.model_validate_json(raw)
        except Exception:
            logger.warning("Cache parse failed for key=%s", self._key)
            return None

    def set(self, value: T) -> None:
        """캐시에 저장."""
        data = value.model_dump_json()
        if self._ttl:
            self._client.setex(self._key, self._ttl, data)
        else:
            self._client.set(self._key, data)

    def delete(self) -> None:
        """캐시 삭제."""
        self._client.delete(self._key)

    def exists(self) -> bool:
        """키 존재 여부."""
        return bool(self._client.exists(self._key))


class TypedHashCache(Generic[T]):
    """Redis Hash 기반 타입 캐시 — 필드별 모델 저장.

    Usage:
        cache = TypedHashCache(redis, "sector_budget:active", SectorBudgetEntry)
        cache.hset("반도체/IT", entry)
        entry = cache.hget("반도체/IT")
    """

    def __init__(
        self,
        client: redis.Redis,
        key: str,
        model_class: type[T],
        ttl: int | None = None,
    ):
        self._client = client
        self._key = key
        self._model_class = model_class
        self._ttl = ttl

    def hset(self, field: str, value: T) -> None:
        self._client.hset(self._key, field, value.model_dump_json())
        if self._ttl:
            self._client.expire(self._key, self._ttl)

    def hget(self, field: str) -> T | None:
        raw = self._client.hget(self._key, field)
        if raw is None:
            return None
        try:
            return self._model_class.model_validate_json(raw)
        except Exception:
            return None

    def hgetall(self) -> dict[str, T]:
        raw = self._client.hgetall(self._key)
        result: dict[str, T] = {}
        for field, data in raw.items():
            try:
                result[field] = self._model_class.model_validate_json(data)
            except Exception:
                continue
        return result

    def set_all(self, values: dict[str, T]) -> None:
        """전체 해시 교체."""
        pipe = self._client.pipeline()
        pipe.delete(self._key)
        for field, value in values.items():
            pipe.hset(self._key, field, value.model_dump_json())
        if self._ttl:
            pipe.expire(self._key, self._ttl)
        pipe.execute()
