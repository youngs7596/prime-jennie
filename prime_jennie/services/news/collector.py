"""News Collector — 네이버 금융 뉴스 + RSS 크롤링 → Redis Stream 발행.

Usage:
    collector = NewsCollector(redis_client, stock_universe)
    count = await collector.run_once()
"""

import logging

import redis

from prime_jennie.domain.news import NewsArticle
from prime_jennie.infra.crawlers.naver import crawl_stock_news

from .dedup import NewsDeduplicator

logger = logging.getLogger(__name__)

NEWS_STREAM = "stream:news:raw"
NEWS_STREAM_MAXLEN = 100_000


class NewsCollector:
    """뉴스 수집기.

    Args:
        redis_client: Redis 클라이언트
        universe: 수집 대상 종목 {code: name}
        max_pages: 종목당 최대 크롤링 페이지
        request_delay: 요청 간 딜레이 (초)
    """

    def __init__(
        self,
        redis_client: redis.Redis,
        universe: dict[str, str] | None = None,
        max_pages: int = 2,
        request_delay: float = 0.3,
    ):
        self._redis = redis_client
        self._universe = universe or {}
        self._max_pages = max_pages
        self._request_delay = request_delay
        self._dedup = NewsDeduplicator(redis_client)

    def run_once(self) -> int:
        """한 번 수집 실행. 발행된 뉴스 건수 반환."""
        total = 0

        for code, name in self._universe.items():
            try:
                articles = crawl_stock_news(code, name, self._max_pages, self._request_delay)
                published = self._publish_batch(articles)
                total += published
            except Exception:
                logger.warning("[%s] Crawl failed", code)

        logger.info("News collector: %d articles published", total)
        return total

    def _publish_batch(self, articles: list[NewsArticle]) -> int:
        """Redis Stream에 뉴스 배치 발행 (중복 제거)."""
        count = 0
        pipe = self._redis.pipeline()

        for article in articles:
            if not self._dedup.is_new(article.article_url):
                continue

            payload = {
                "stock_code": article.stock_code,
                "stock_name": article.stock_name,
                "headline": article.headline,
                "press": article.press,
                "summary": article.summary or "",
                "article_url": article.article_url,
                "published_at": article.published_at.isoformat(),
                "source": article.source,
            }

            pipe.xadd(NEWS_STREAM, payload, maxlen=NEWS_STREAM_MAXLEN)
            count += 1

        if count > 0:
            pipe.execute()

        return count
