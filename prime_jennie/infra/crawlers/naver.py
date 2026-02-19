"""네이버 금융 크롤러 — 종목 뉴스 + 섹터 분류.

Usage:
    articles = crawl_stock_news("005930", "삼성전자", max_pages=2)
    mapping = build_naver_sector_mapping()
"""

import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from prime_jennie.domain.news import NewsArticle

logger = logging.getLogger(__name__)

NAVER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

# In-memory dedup (per-process)
_seen_hashes: set[str] = set()


def _compute_hash(text: str) -> str:
    """텍스트 해시 (중복 체크용)."""
    normalized = text.strip().lower()
    return hashlib.md5(normalized.encode()).hexdigest()[:12]


def crawl_stock_news(
    stock_code: str,
    stock_name: str,
    max_pages: int = 2,
    request_delay: float = 0.3,
) -> list[NewsArticle]:
    """네이버 금융 종목 뉴스 크롤링.

    Args:
        stock_code: 6자리 종목코드
        stock_name: 종목명
        max_pages: 최대 페이지 수
        request_delay: 요청 간 딜레이 (초)
    """
    articles: list[NewsArticle] = []
    base_url = "https://finance.naver.com/item/news_news.naver"

    for page in range(1, max_pages + 1):
        try:
            resp = httpx.get(
                base_url,
                params={"code": stock_code, "page": page},
                headers=NAVER_HEADERS,
                timeout=10,
            )
            resp.encoding = "euc-kr"
            soup = BeautifulSoup(resp.text, "html.parser")

            rows = soup.select("table.type5 tbody tr")
            if not rows:
                break

            for row in rows:
                link = row.select_one("td.title a")
                if not link:
                    continue

                headline = link.get_text(strip=True)
                if not headline:
                    continue

                # 중복 체크
                h = _compute_hash(headline)
                if h in _seen_hashes:
                    continue
                _seen_hashes.add(h)

                href = link.get("href", "")
                article_url = f"https://finance.naver.com{href}" if href.startswith("/") else href

                press_td = row.select_one("td.info")
                press = press_td.get_text(strip=True) if press_td else ""

                date_td = row.select_one("td.date")
                date_str = date_td.get_text(strip=True) if date_td else ""

                published_at = datetime.now(timezone.utc)
                if date_str:
                    try:
                        published_at = datetime.strptime(date_str, "%Y.%m.%d %H:%M")
                        published_at = published_at.replace(tzinfo=timezone.utc)
                    except ValueError:
                        pass

                articles.append(
                    NewsArticle(
                        stock_code=stock_code,
                        stock_name=stock_name,
                        press=press,
                        headline=headline,
                        article_url=article_url,
                        published_at=published_at,
                        source="NAVER",
                    )
                )

            time.sleep(request_delay)

        except Exception as e:
            logger.warning("[%s] News crawl page %d failed: %s", stock_code, page, e)
            break

    return articles


def build_naver_sector_mapping() -> dict[str, str]:
    """네이버 업종 분류 크롤링 (79개 세분류 → 종목 매핑).

    Returns:
        {stock_code: sector_name, ...}
    """
    mapping: dict[str, str] = {}
    base_url = "https://finance.naver.com/sise/sise_group.naver"

    try:
        resp = httpx.get(base_url, params={"type": "upjong"}, headers=NAVER_HEADERS, timeout=15)
        resp.encoding = "euc-kr"
        soup = BeautifulSoup(resp.text, "html.parser")

        sector_links = soup.select("table.type_1 td a[href*='no=']")
        for link in sector_links:
            sector_name = link.get_text(strip=True)
            href = link.get("href", "")
            if "no=" not in href:
                continue

            sector_no = href.split("no=")[-1].split("&")[0]
            stocks = _get_sector_stocks(sector_no)
            for code in stocks:
                mapping[code] = sector_name

            time.sleep(0.2)

    except Exception as e:
        logger.error("Sector mapping crawl failed: %s", e)

    logger.info("Naver sector mapping: %d stocks mapped", len(mapping))
    return mapping


def _get_sector_stocks(sector_no: str) -> list[str]:
    """업종 내 종목 코드 목록."""
    url = "https://finance.naver.com/sise/sise_group_detail.naver"
    try:
        resp = httpx.get(url, params={"type": "upjong", "no": sector_no},
                         headers=NAVER_HEADERS, timeout=10)
        resp.encoding = "euc-kr"
        soup = BeautifulSoup(resp.text, "html.parser")

        codes = []
        for link in soup.select("table.type_5 td a[href*='code=']"):
            href = link.get("href", "")
            if "code=" in href:
                code = href.split("code=")[-1].split("&")[0]
                if len(code) == 6 and code.isdigit():
                    codes.append(code)
        return codes

    except Exception:
        return []


def clear_news_hash_cache() -> None:
    """테스트용: in-memory 해시 캐시 초기화."""
    _seen_hashes.clear()
