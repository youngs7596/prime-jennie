"""네이버 뉴스 매크로/지정학 헤드라인 크롤러.

Council pipeline의 political_news 입력으로 사용.
경제(101)/세계(104) 섹션 헤드라인에서 매크로·지정학 키워드를 필터링.
"""

import logging

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

# 네이버 뉴스 섹션 ID
_SECTIONS = {
    "경제": 101,
    "세계": 104,
}

# 매크로·지정학 키워드 — 하나라도 포함된 헤드라인만 수집
_FILTER_KEYWORDS = [
    # 지정학
    "전쟁",
    "중동",
    "이란",
    "이스라엘",
    "지정학",
    "공습",
    "미사일",
    "제재",
    "파병",
    "폭격",
    "핵",
    "NATO",
    "우크라이나",
    "러시아",
    "대만",
    "북한",
    # 매크로
    "금리",
    "연준",
    "FOMC",
    "인플레이션",
    "CPI",
    "관세",
    "트럼프",
    "원유",
    "유가",
    "WTI",
    "환율",
    "달러",
    "VIX",
    "경기침체",
    "GDP",
]


def fetch_macro_headlines(max_per_section: int = 15) -> list[str]:
    """네이버 뉴스 경제/세계 섹션에서 매크로·지정학 헤드라인 수집.

    Returns:
        키워드 매칭된 헤드라인 문자열 리스트 (최대 20개).
    """
    headlines: list[str] = []

    for section_name, section_id in _SECTIONS.items():
        try:
            fetched = _fetch_section_headlines(section_id, max_per_section)
            for title in fetched:
                if any(kw in title for kw in _FILTER_KEYWORDS):
                    headlines.append(f"[{section_name}] {title}")
        except Exception:
            logger.warning("네이버 뉴스 %s 섹션 크롤링 실패", section_name, exc_info=True)

    # 중복 제거 + 최대 20개
    seen = set()
    unique = []
    for h in headlines:
        if h not in seen:
            seen.add(h)
            unique.append(h)
    return unique[:20]


def _fetch_section_headlines(section_id: int, max_count: int) -> list[str]:
    """네이버 뉴스 섹션 페이지에서 헤드라인 추출."""
    url = f"https://news.naver.com/section/{section_id}"
    with httpx.Client(headers=_HEADERS, timeout=10, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    titles: list[str] = []

    # 헤드라인 추출: <strong class="sa_text_strong"> 태그
    for tag in soup.select("strong.sa_text_strong"):
        text = tag.get_text(strip=True)
        if text and len(text) >= 10:
            titles.append(text)
            if len(titles) >= max_count:
                break

    return titles
