"""뉴스 및 감성 분석 모델."""

from datetime import date, datetime

from pydantic import BaseModel

from .types import Score, StockCode


class NewsArticle(BaseModel):
    """수집된 뉴스 기사."""

    stock_code: StockCode
    stock_name: str
    press: str
    headline: str
    summary: str | None = None
    category: str | None = None  # 실적, 수주, 규제 등
    article_url: str
    published_at: datetime
    source: str  # NAVER, DAUM, etc.


class NewsSentiment(BaseModel):
    """뉴스 감성 분석 결과."""

    stock_code: StockCode
    news_date: date
    press: str
    headline: str
    summary: str | None = None
    sentiment_score: Score  # 0=극부정, 50=중립, 100=극긍정
    sentiment_reason: str | None = None
    category: str | None = None
    article_url: str  # Unique constraint
    published_at: datetime
