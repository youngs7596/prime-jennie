"""뉴스 파이프라인 단위 테스트."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from prime_jennie.domain.news import NewsArticle

# ─── NewsDeduplicator ────────────────────────────────────────


class TestNewsDeduplicator:
    def test_is_new_returns_true_first_time(self):
        from prime_jennie.services.news.dedup import NewsDeduplicator

        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        # 최근 3일 키 모두에서 미발견
        mock_pipe.execute.side_effect = [
            [False, False, False],  # is_duplicate → not found
            None,  # mark_seen
        ]
        dedup = NewsDeduplicator(mock_redis)

        assert dedup.is_new("https://example.com/article1") is True

    def test_is_duplicate_returns_true_for_seen(self):
        from prime_jennie.services.news.dedup import NewsDeduplicator

        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        # 3일 중 하나에서 발견
        mock_pipe.execute.return_value = [True, False, False]
        dedup = NewsDeduplicator(mock_redis)

        assert dedup.is_duplicate("https://example.com/article1") is True

    def test_is_duplicate_checks_all_3_days(self):
        """어제 수집한 뉴스도 중복 감지."""
        from prime_jennie.services.news.dedup import NewsDeduplicator

        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        # 오늘은 없지만 어제 키에서 발견
        mock_pipe.execute.return_value = [False, True, False]
        dedup = NewsDeduplicator(mock_redis)

        assert dedup.is_duplicate("https://example.com/article1") is True

    def test_mark_seen_calls_redis(self):
        from prime_jennie.services.news.dedup import NewsDeduplicator

        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        dedup = NewsDeduplicator(mock_redis)

        dedup.mark_seen("https://example.com/article1")
        mock_pipe.sadd.assert_called_once()
        mock_pipe.expire.assert_called_once()
        mock_pipe.execute.assert_called_once()

    def test_is_duplicate_returns_false_on_error(self):
        from prime_jennie.services.news.dedup import NewsDeduplicator

        mock_redis = MagicMock()
        mock_redis.pipeline.side_effect = Exception("connection refused")
        dedup = NewsDeduplicator(mock_redis)

        assert dedup.is_duplicate("url") is False


# ─── NewsCollector ────────────────────────────────────────────


class TestNewsCollector:
    def test_publish_batch_dedup(self):
        from prime_jennie.services.news.collector import NewsCollector

        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        # is_duplicate → not found → mark_seen → xadd publish
        mock_pipe.execute.side_effect = [
            [False, False, False],  # is_duplicate
            None,  # mark_seen
            None,  # xadd
        ]

        collector = NewsCollector(mock_redis, {"005930": "삼성전자"})

        articles = [
            NewsArticle(
                stock_code="005930",
                stock_name="삼성전자",
                press="한경",
                headline="삼성전자 실적 호재",
                article_url="https://example.com/1",
                published_at=datetime.now(UTC),
                source="NAVER",
            )
        ]

        count = collector._publish_batch(articles)
        assert count == 1
        mock_pipe.xadd.assert_called_once()

    def test_publish_batch_skips_duplicates(self):
        from prime_jennie.services.news.collector import NewsCollector

        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        # 3일 중 하나에서 발견 → 중복
        mock_pipe.execute.return_value = [True, False, False]

        collector = NewsCollector(mock_redis)
        articles = [
            NewsArticle(
                stock_code="005930",
                stock_name="삼성전자",
                press="한경",
                headline="이미 수집된 뉴스",
                article_url="https://example.com/dup",
                published_at=datetime.now(UTC),
                source="NAVER",
            )
        ]

        count = collector._publish_batch(articles)
        assert count == 0


# ─── NewsAnalyzer ─────────────────────────────────────────────


class TestNewsAnalyzer:
    @patch("prime_jennie.domain.config.get_config")
    def test_emergency_keyword_detection(self, mock_config):
        from prime_jennie.services.news.analyzer import EMERGENCY_KEYWORDS

        headline = "속보: 한미 관세 합의"
        assert any(kw in headline for kw in EMERGENCY_KEYWORDS)

    @patch("prime_jennie.domain.config.get_config")
    def test_decode_bytes_and_str(self, mock_config):
        from prime_jennie.services.news.analyzer import NewsAnalyzer

        mock_redis = MagicMock()
        mock_redis.xgroup_create.side_effect = Exception("BUSYGROUP")

        analyzer = NewsAnalyzer.__new__(NewsAnalyzer)
        analyzer._redis = mock_redis
        analyzer._llm = MagicMock()
        analyzer._session_factory = None

        # bytes key
        assert analyzer._decode({b"headline": b"test"}, "headline") == "test"
        # str key
        assert analyzer._decode({"headline": "test2"}, "headline") == "test2"
        # missing key
        assert analyzer._decode({}, "missing", "default") == "default"


# ─── NewsArchiver ──────────────────────────────────────────


class TestNewsArchiver:
    def test_is_archived_returns_false_for_new(self):
        from prime_jennie.services.news.archiver import NewsArchiver

        mock_redis = MagicMock()
        mock_redis.sismember.return_value = False
        mock_redis.xgroup_create.return_value = None

        archiver = NewsArchiver(mock_redis)
        assert archiver._is_archived("https://example.com/new") is False

    def test_is_archived_returns_true_for_seen(self):
        from prime_jennie.services.news.archiver import NewsArchiver

        mock_redis = MagicMock()
        mock_redis.sismember.return_value = True
        mock_redis.xgroup_create.return_value = None

        archiver = NewsArchiver(mock_redis)
        assert archiver._is_archived("https://example.com/seen") is True

    def test_mark_archived_calls_redis(self):
        from prime_jennie.services.news.archiver import NewsArchiver

        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        mock_redis.xgroup_create.return_value = None

        archiver = NewsArchiver(mock_redis)
        archiver._mark_archived("https://example.com/article")
        mock_pipe.sadd.assert_called_once()
        mock_pipe.expire.assert_called_once()
        mock_pipe.execute.assert_called_once()


# ─── Naver Crawler ──────────────────────────────────────────


class TestNaverCrawler:
    def test_compute_hash(self):
        from prime_jennie.infra.crawlers.naver import _compute_hash

        h1 = _compute_hash("삼성전자 실적")
        h2 = _compute_hash("삼성전자 실적")
        h3 = _compute_hash("삼성전자 배당")

        assert h1 == h2
        assert h1 != h3
        assert len(h1) == 12

    def test_clear_hash_cache(self):
        from prime_jennie.infra.crawlers.naver import _seen_hashes, clear_news_hash_cache

        _seen_hashes.add("test")
        clear_news_hash_cache()
        assert len(_seen_hashes) == 0

    def test_noise_filter(self):
        from prime_jennie.infra.crawlers.naver import _is_noise_title

        assert _is_noise_title("오전 시황 코스피 상승") is True
        assert _is_noise_title("[이슈종합] 삼성전자") is True
        assert _is_noise_title("삼성전자 AI 에이전트 발표") is False
