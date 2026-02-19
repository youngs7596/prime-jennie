"""News Analyzer — Redis Stream → LLM 감성 분석 → DB 저장.

뉴스 감성 분석 + 경쟁사 리스크 탐지.
"""

import contextlib
import logging
from datetime import UTC, datetime

import redis

from prime_jennie.infra.database.models import StockNewsSentimentDB
from prime_jennie.infra.llm.base import BaseLLMProvider

logger = logging.getLogger(__name__)

NEWS_STREAM = "stream:news:raw"
ANALYZER_GROUP = "group_analyzer"
ANALYZER_CONSUMER = "analyzer_1"
BLOCK_MS = 2000

# 긴급 키워드 (fast-track)
EMERGENCY_KEYWORDS = frozenset(
    [
        "속보",
        "긴급",
        "전쟁",
        "관세",
        "Emergency",
        "Breaking",
        "파병",
        "계엄",
        "공습",
        "폭격",
    ]
)

SENTIMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "minimum": 0, "maximum": 100},
        "reason": {"type": "string"},
    },
    "required": ["score", "reason"],
}


class NewsAnalyzer:
    """뉴스 감성 분석기.

    Args:
        redis_client: Redis 클라이언트
        llm_provider: LLM provider (FAST tier)
        db_session_factory: DB 세션 팩토리
    """

    def __init__(
        self,
        redis_client: redis.Redis,
        llm_provider: BaseLLMProvider,
        db_session_factory=None,
    ):
        self._redis = redis_client
        self._llm = llm_provider
        self._session_factory = db_session_factory
        self._ensure_consumer_group()

    def _ensure_consumer_group(self) -> None:
        try:
            self._redis.xgroup_create(NEWS_STREAM, ANALYZER_GROUP, id="0", mkstream=True)
        except redis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

    def run_once(self, max_messages: int = 100) -> int:
        """한 번 실행. 분석 완료 건수 반환."""
        processed = 0

        # Pending 복구
        processed += self._process_pending()

        # 신규 메시지
        while processed < max_messages:
            messages = self._redis.xreadgroup(
                ANALYZER_GROUP,
                ANALYZER_CONSUMER,
                {NEWS_STREAM: ">"},
                count=1,
                block=BLOCK_MS,
            )
            if not messages:
                break

            for _stream_name, entries in messages:
                for msg_id, data in entries:
                    try:
                        self._analyze_message(data)
                    except Exception:
                        logger.warning("Analysis failed for %s", msg_id)
                    finally:
                        self._redis.xack(NEWS_STREAM, ANALYZER_GROUP, msg_id)
                        processed += 1

        return processed

    def _process_pending(self) -> int:
        """미ACK 메시지 복구."""
        count = 0
        while True:
            pending = self._redis.xreadgroup(
                ANALYZER_GROUP,
                ANALYZER_CONSUMER,
                {NEWS_STREAM: "0"},
                count=10,
            )
            if not pending:
                break

            has_messages = False
            for _stream_name, entries in pending:
                if not entries:
                    continue
                has_messages = True
                for msg_id, data in entries:
                    try:
                        self._analyze_message(data)
                    except Exception:
                        pass
                    finally:
                        self._redis.xack(NEWS_STREAM, ANALYZER_GROUP, msg_id)
                        count += 1

            if not has_messages:
                break

        return count

    def _decode(self, data: dict, key: str, default: str = "") -> str:
        """bytes/str 양쪽 지원 디코딩."""
        val = data.get(key, data.get(key.encode(), default))
        if isinstance(val, bytes):
            return val.decode()
        return str(val) if val else default

    def _analyze_message(self, data: dict) -> None:
        """단일 뉴스 분석 + DB 저장."""
        headline = self._decode(data, "headline")
        stock_code = self._decode(data, "stock_code")
        article_url = self._decode(data, "article_url")
        press = self._decode(data, "press")
        published_str = self._decode(data, "published_at")

        if not headline or not stock_code:
            return

        # DB 중복 체크 (이미 분석된 URL)
        if self._session_factory and self._is_url_exists(article_url):
            return

        # LLM 감성 분석
        is_emergency = any(kw in headline for kw in EMERGENCY_KEYWORDS)
        sentiment = self._analyze_sentiment(headline, stock_code, is_emergency)

        # DB 저장
        if self._session_factory and sentiment:
            published_at = datetime.now(UTC)
            if published_str:
                with contextlib.suppress(ValueError):
                    published_at = datetime.fromisoformat(published_str)

            self._save_sentiment(
                stock_code=stock_code,
                headline=headline,
                press=press,
                score=sentiment["score"],
                reason=sentiment.get("reason", ""),
                article_url=article_url,
                published_at=published_at,
            )

    def _analyze_sentiment(self, headline: str, stock_code: str, is_emergency: bool = False) -> dict | None:
        """LLM 감성 분석. 실패 시 기본값 반환."""
        import asyncio

        prompt = (
            f"다음 한국 주식 뉴스의 감성을 분석하세요.\n"
            f"종목코드: {stock_code}\n"
            f"헤드라인: {headline}\n\n"
            f"score(0-100, 50=중립)와 reason(한국어 1문장)을 JSON으로 반환."
        )

        try:
            loop = asyncio.new_event_loop()
            result = loop.run_until_complete(
                self._llm.generate_json(
                    prompt=prompt,
                    schema=SENTIMENT_SCHEMA,
                    model=None,
                    service="news_analysis",
                )
            )
            loop.close()

            if isinstance(result, dict) and "score" in result:
                score = max(0, min(100, int(result["score"])))
                return {"score": score, "reason": result.get("reason", "")}
        except Exception:
            logger.debug("[%s] Sentiment LLM failed, using default", stock_code)

        # 기본값: 중립
        return {"score": 50, "reason": "분석 불가 — 기본 중립"}

    def _is_url_exists(self, article_url: str) -> bool:
        """DB에 이미 존재하는 URL인지 확인."""
        if not self._session_factory:
            return False
        try:
            with self._session_factory() as session:
                from sqlmodel import select

                stmt = select(StockNewsSentimentDB).where(StockNewsSentimentDB.article_url == article_url).limit(1)
                return session.exec(stmt).first() is not None
        except Exception:
            return False

    def _save_sentiment(
        self,
        stock_code: str,
        headline: str,
        press: str,
        score: int,
        reason: str,
        article_url: str,
        published_at: datetime,
    ) -> None:
        """감성 분석 결과 DB 저장."""
        if not self._session_factory:
            return
        try:
            with self._session_factory() as session:
                record = StockNewsSentimentDB(
                    stock_code=stock_code,
                    news_date=published_at.date(),
                    press=press,
                    headline=headline[:500],
                    sentiment_score=score,
                    sentiment_reason=reason[:2000] if reason else None,
                    article_url=article_url[:1000],
                    published_at=published_at,
                    source="ANALYZER",
                )
                session.add(record)
                session.commit()
        except Exception as e:
            logger.warning("[%s] Save sentiment failed: %s", stock_code, e)
