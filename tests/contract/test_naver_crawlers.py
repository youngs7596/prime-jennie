"""Contract smoke tests — 네이버 금융 크롤러.

Sentinel 종목(삼성전자 005930)으로 실제 HTTP 요청을 보내
HTML 구조 변경 및 파싱 오류를 감지한다.

실행: pytest tests/contract/test_naver_crawlers.py -v
CI:   주간 cron (schedule) 트리거에서만 실행.
"""

import pytest

from prime_jennie.infra.crawlers.naver import (
    NaverFundamentals,
    build_naver_sector_mapping,
    crawl_naver_fundamentals,
    crawl_naver_roe,
    crawl_stock_news,
)

SENTINEL_CODE = "005930"
SENTINEL_NAME = "삼성전자"


# ── fixtures (같은 페이지 중복 요청 방지) ──────────────────────


@pytest.fixture(scope="module")
def fundamentals() -> NaverFundamentals | None:
    return crawl_naver_fundamentals(SENTINEL_CODE)


@pytest.fixture(scope="module")
def roe_value() -> float | None:
    return crawl_naver_roe(SENTINEL_CODE)


@pytest.fixture(scope="module")
def news_articles():
    return crawl_stock_news(SENTINEL_CODE, SENTINEL_NAME, max_pages=1)


@pytest.fixture(scope="module")
def sector_mapping():
    return build_naver_sector_mapping()


# ── crawl_naver_fundamentals ──────────────────────────────────


class TestFundamentals:
    def test_returns_data(self, fundamentals):
        assert fundamentals is not None, "crawl_naver_fundamentals returned None"

    def test_per_range(self, fundamentals):
        if fundamentals is None:
            pytest.skip("no data")
        assert fundamentals.per is not None, "PER is None"
        assert 1 < fundamentals.per < 200, f"PER out of range: {fundamentals.per}"

    def test_pbr_range(self, fundamentals):
        if fundamentals is None:
            pytest.skip("no data")
        assert fundamentals.pbr is not None, "PBR is None"
        assert 0.1 < fundamentals.pbr < 50, f"PBR out of range: {fundamentals.pbr}"

    def test_roe_range(self, fundamentals):
        if fundamentals is None:
            pytest.skip("no data")
        if fundamentals.roe is None:
            pytest.skip("ROE not available this quarter")
        assert -50 < fundamentals.roe < 100, f"ROE out of range: {fundamentals.roe}"

    def test_quarter_name_format(self, fundamentals):
        if fundamentals is None:
            pytest.skip("no data")
        assert fundamentals.quarter_name is not None
        # "YYYY.MM" 형식
        parts = fundamentals.quarter_name.split(".")
        assert len(parts) == 2, f"unexpected format: {fundamentals.quarter_name}"
        assert parts[0].isdigit() and len(parts[0]) == 4
        assert parts[1].isdigit() and len(parts[1]) == 2


# ── crawl_naver_roe ───────────────────────────────────────────


class TestROE:
    def test_returns_float(self, roe_value):
        assert roe_value is not None, "crawl_naver_roe returned None"
        assert isinstance(roe_value, float)

    def test_roe_range(self, roe_value):
        if roe_value is None:
            pytest.skip("no data")
        assert -50 < roe_value < 100, f"ROE out of range: {roe_value}"


# ── crawl_stock_news ──────────────────────────────────────────


class TestStockNews:
    def test_returns_articles(self, news_articles):
        assert len(news_articles) > 0, "no news articles returned"

    def test_article_fields(self, news_articles):
        if not news_articles:
            pytest.skip("no articles")
        article = news_articles[0]
        assert article.headline, "headline is empty"
        assert article.article_url.startswith("http"), f"bad url: {article.article_url}"
        assert article.stock_code == SENTINEL_CODE


# ── build_naver_sector_mapping ────────────────────────────────


class TestSectorMapping:
    def test_mapping_size(self, sector_mapping):
        assert len(sector_mapping) >= 500, f"too few stocks: {len(sector_mapping)}"

    def test_sentinel_included(self, sector_mapping):
        assert SENTINEL_CODE in sector_mapping, "삼성전자 not in mapping"

    def test_sector_name_nonempty(self, sector_mapping):
        if SENTINEL_CODE not in sector_mapping:
            pytest.skip("sentinel missing")
        assert sector_mapping[SENTINEL_CODE], "sector name is empty"
