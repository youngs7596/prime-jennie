"""뉴스 중복 제거 — Redis SET 기반 3일 TTL.

Usage:
    dedup = NewsDeduplicator(redis_client)
    if dedup.is_duplicate("https://example.com/article"):
        skip()
"""

import hashlib
import logging
from datetime import date

import redis

logger = logging.getLogger(__name__)

_DEDUP_TTL = 3 * 86400  # 3 days


class NewsDeduplicator:
    """Redis SET 기반 뉴스 중복 체크.

    날짜별 SET: dedup:news:YYYYMMDD
    """

    def __init__(self, redis_client: redis.Redis):
        self._redis = redis_client

    def _key(self) -> str:
        return f"dedup:news:{date.today().strftime('%Y%m%d')}"

    def _hash(self, text: str) -> str:
        return hashlib.md5(text.strip().lower().encode()).hexdigest()[:12]

    def is_duplicate(self, url_or_text: str) -> bool:
        """중복 여부 확인 (True=이미 처리됨)."""
        try:
            h = self._hash(url_or_text)
            return bool(self._redis.sismember(self._key(), h))
        except Exception:
            return False

    def mark_seen(self, url_or_text: str) -> None:
        """처리 완료 마킹."""
        try:
            h = self._hash(url_or_text)
            key = self._key()
            pipe = self._redis.pipeline()
            pipe.sadd(key, h)
            pipe.expire(key, _DEDUP_TTL)
            pipe.execute()
        except Exception:
            logger.debug("Failed to mark news as seen")

    def is_new(self, url_or_text: str) -> bool:
        """신규 여부 + 마킹 (원자적)."""
        if self.is_duplicate(url_or_text):
            return False
        self.mark_seen(url_or_text)
        return True
