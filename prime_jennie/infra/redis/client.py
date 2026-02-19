"""Redis client factory."""

from functools import lru_cache

import redis

from prime_jennie.domain.config import get_config


@lru_cache
def get_redis() -> redis.Redis:
    """프로세스 전역 Redis 클라이언트 (싱글턴).

    테스트에서는 get_redis.cache_clear() 후 재생성.
    """
    config = get_config()
    return redis.Redis.from_url(
        config.redis.url,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=15,
        retry_on_timeout=True,
    )
