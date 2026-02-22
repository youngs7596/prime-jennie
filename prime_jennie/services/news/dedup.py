"""뉴스 중복 제거 — Redis SET 기반 3일 TTL.

날짜별 SET으로 관리하되, 중복 체크 시 최근 3일 키를 모두 확인.

Usage:
    dedup = NewsDeduplicator(redis_client)
    if dedup.is_duplicate("https://example.com/article"):
        skip()
"""

import hashlib
import logging
from datetime import date, timedelta

import redis

logger = logging.getLogger(__name__)

_DEDUP_TTL = 3 * 86400  # 3 days
_DEDUP_DAYS = 3  # 최근 3일 키 체크


class NewsDeduplicator:
    """Redis SET 기반 뉴스 중복 체크.

    날짜별 SET: dedup:news:YYYYMMDD
    중복 확인 시 최근 3일 키를 모두 체크하여 날짜 경계 중복 방지.
    """

    def __init__(self, redis_client: redis.Redis):
        self._redis = redis_client

    def _today_key(self) -> str:
        return f"dedup:news:{date.today().strftime('%Y%m%d')}"

    def _recent_keys(self) -> list[str]:
        """최근 3일 키 목록."""
        today = date.today()
        return [f"dedup:news:{(today - timedelta(days=d)).strftime('%Y%m%d')}" for d in range(_DEDUP_DAYS)]

    def _hash(self, text: str) -> str:
        return hashlib.md5(text.strip().lower().encode()).hexdigest()[:12]

    def is_duplicate(self, url_or_text: str) -> bool:
        """중복 여부 확인 — 최근 3일 키 모두 체크."""
        try:
            h = self._hash(url_or_text)
            pipe = self._redis.pipeline()
            for key in self._recent_keys():
                pipe.sismember(key, h)
            return any(pipe.execute())
        except Exception:
            return False

    def mark_seen(self, url_or_text: str) -> None:
        """오늘 키에 처리 완료 마킹."""
        try:
            h = self._hash(url_or_text)
            key = self._today_key()
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
