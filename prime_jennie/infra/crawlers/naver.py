"""네이버 금융 크롤러 — 종목 뉴스 + 섹터 분류.

my-prime-jennie shared/crawlers/naver.py 기반 재구현.

Usage:
    articles = crawl_stock_news("005930", "삼성전자", max_pages=2)
    mapping = build_naver_sector_mapping()
"""

import hashlib
import logging
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime

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

# 노이즈 뉴스 필터링 키워드 (시황/특징주 등 투자 판단에 무의미한 뉴스)
NOISE_KEYWORDS = [
    "특징주",
    "오전 시황",
    "장마감",
    "마감 시황",
    "급등락",
    "오늘의 증시",
    "환율",
    "개장",
    "출발",
    "상위 종목",
    "단독",
    "인포",
    "증권리포트",
    "장중시황",
    "[이슈종합]",
    "인기 기업",
    "한줄리포트",
    "이 시각 증권",
]

# In-memory dedup (per-process)
_seen_hashes: set[str] = set()


def _compute_hash(text: str) -> str:
    """뉴스 제목으로 중복 체크용 해시 생성."""
    normalized = re.sub(r"[^\w]", "", text.lower())
    return hashlib.md5(normalized.encode()).hexdigest()[:12]


def _is_noise_title(title: str) -> bool:
    """노이즈 뉴스인지 확인."""
    return any(kw in title for kw in NOISE_KEYWORDS)


def crawl_stock_news(
    stock_code: str,
    stock_name: str,
    max_pages: int = 2,
    request_delay: float = 0.3,
) -> list[NewsArticle]:
    """네이버 금융 종목 뉴스 크롤링.

    my-prime-jennie shared/crawlers/naver.py crawl_stock_news() 기반.
    핵심: Referer 헤더 필수, tbody 미사용(네이버 금융에 없음), 노이즈 필터링.

    Args:
        stock_code: 6자리 종목코드
        stock_name: 종목명
        max_pages: 최대 페이지 수
        request_delay: 요청 간 딜레이 (초)
    """
    articles: list[NewsArticle] = []
    headers = {
        **NAVER_HEADERS,
        "Referer": f"https://finance.naver.com/item/news.naver?code={stock_code}",
    }

    for page in range(1, max_pages + 1):
        try:
            url = f"https://finance.naver.com/item/news_news.naver?code={stock_code}&page={page}"
            resp = httpx.get(url, headers=headers, timeout=10)
            resp.encoding = "euc-kr"
            soup = BeautifulSoup(resp.text, "html.parser")

            news_table = soup.select_one("table.type5")
            if not news_table:
                break

            # tbody 사용 금지 — 네이버 금융 HTML에 tbody 없음
            rows = news_table.select("tr")
            page_count = 0

            for row in rows:
                title_td = row.select_one("td.title")
                if not title_td:
                    continue

                link = title_td.select_one("a")
                if not link:
                    continue

                headline = link.get_text(strip=True)
                if not headline:
                    continue

                # 노이즈 필터링
                if _is_noise_title(headline):
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

                published_at = datetime.now(UTC)
                if date_str:
                    try:
                        published_at = datetime.strptime(date_str, "%Y.%m.%d %H:%M")
                        published_at = published_at.replace(tzinfo=UTC)
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
                page_count += 1

            logger.debug("[%s] page %d: %d articles", stock_code, page, page_count)

            if page < max_pages:
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
        resp = httpx.get(url, params={"type": "upjong", "no": sector_no}, headers=NAVER_HEADERS, timeout=10)
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


def crawl_naver_roe(stock_code: str) -> float | None:
    """네이버 금융 종목 메인 페이지에서 ROE(%) 파싱.

    테이블 구조: table.tb_type1.tb_num 내 th에 "ROE" 포함 행.
    - 간단 테이블(ROE(%)): TD[0]에 최근 값
    - 상세 테이블(ROE(지배주주)): 여러 분기, 첫 번째 유효 값 사용

    Returns:
        ROE (float, e.g. 12.34) or None if not found.
    """
    url = f"https://finance.naver.com/item/main.naver?code={stock_code}"
    try:
        resp = httpx.get(url, headers=NAVER_HEADERS, timeout=10)
        resp.encoding = "euc-kr"
        soup = BeautifulSoup(resp.text, "html.parser")

        # 모든 테이블에서 ROE 행 검색 (tb_type1 우선, 없으면 전체)
        for table in soup.select("table"):
            for row in table.select("tr"):
                th = row.select_one("th")
                if not th or "ROE" not in th.get_text():
                    continue
                # 첫 번째 유효 td 값 사용
                for td in row.select("td"):
                    text = td.get_text(strip=True).replace(",", "")
                    if text and text not in ("-", "N/A", ""):
                        try:
                            return float(text)
                        except ValueError:
                            continue

    except Exception as e:
        logger.warning("[%s] ROE crawl failed: %s", stock_code, e)

    return None


@dataclass
class NaverFundamentals:
    """네이버 금융 메인 페이지에서 파싱한 분기 재무 데이터."""

    per: float | None = None
    pbr: float | None = None
    roe: float | None = None
    quarter_name: str | None = None  # e.g. "2025.09"


def crawl_naver_fundamentals(stock_code: str) -> NaverFundamentals | None:
    """네이버 금융 메인 페이지에서 최신 실적 분기 PER/PBR/ROE 파싱.

    주요재무정보 테이블에서 (E) 추정치를 제외한 가장 최근 실적 분기를 찾아
    해당 분기의 PER/PBR/ROE를 반환한다.

    Returns:
        NaverFundamentals (최소 1개 유효값) or None.
    """
    url = f"https://finance.naver.com/item/main.naver?code={stock_code}"
    try:
        resp = httpx.get(url, headers=NAVER_HEADERS, timeout=10)
        resp.encoding = "euc-kr"
        soup = BeautifulSoup(resp.text, "html.parser")

        # 주요재무정보 테이블 탐색: EPS/BPS/PER 모두 포함된 테이블
        target_table = None
        for table in soup.select("table"):
            text = table.get_text()
            if "EPS" in text and "BPS" in text and "PER" in text:
                target_table = table
                break

        if not target_table:
            return None

        rows = target_table.select("tr")
        if not rows:
            return None

        # 1) 날짜 행에서 실적(non-E) 분기 인덱스 결정
        actual_col_idx: int | None = None
        quarter_name: str | None = None

        for row in rows:
            ths = row.select("th")
            if not ths:
                continue
            header_texts = [th.get_text(strip=True) for th in ths]
            # 날짜 행: "2024.09", "2024.12(E)" 등의 패턴
            date_ths = [t for t in header_texts if re.match(r"\d{4}\.\d{2}", t)]
            if len(date_ths) < 2:
                continue

            # 오른쪽(최신)부터 탐색, (E) 없는 가장 최근 실적 분기
            for i in range(len(ths) - 1, -1, -1):
                th_text = ths[i].get_text(strip=True)
                if re.match(r"\d{4}\.\d{2}", th_text) and "(E)" not in th_text:
                    actual_col_idx = i
                    quarter_name = th_text
                    break
            break  # 첫 번째 날짜 행만 사용

        if actual_col_idx is None:
            return None

        # 2) 각 지표 행에서 해당 분기 값 추출
        per_val: float | None = None
        pbr_val: float | None = None
        roe_val: float | None = None

        for row in rows:
            first = row.select_one("th")
            if not first:
                continue
            label = first.get_text(strip=True)
            tds = row.select("td")
            if not tds:
                continue

            # 날짜 행은 th만으로 구성, 데이터 행은 th(라벨)+td → 동일 인덱스
            td_idx = actual_col_idx
            if td_idx >= len(tds):
                continue

            raw = tds[td_idx].get_text(strip=True).replace(",", "")
            if not raw or raw in ("-", "N/A", ""):
                continue

            try:
                value = float(raw)
            except ValueError:
                continue

            if "PER" in label and "EPS" not in label:
                per_val = value
            elif "PBR" in label and "BPS" not in label:
                pbr_val = value
            elif "ROE" in label:
                roe_val = value

        if per_val is None and pbr_val is None and roe_val is None:
            return None

        return NaverFundamentals(
            per=per_val,
            pbr=pbr_val,
            roe=roe_val,
            quarter_name=quarter_name,
        )

    except Exception as e:
        logger.warning("[%s] Fundamentals crawl failed: %s", stock_code, e)

    return None


def clear_news_hash_cache() -> None:
    """테스트용: in-memory 해시 캐시 초기화."""
    _seen_hashes.clear()
