"""네이버 금융 종목별 외국인/기관 수급 크롤러.

pykrx (data.krx.co.kr) 장애 대체용 — 종목별 투자자 순매매 + 외국인 보유율.
frgn.naver 페이지 단일 파싱으로 두 Job 모두 지원.

Usage:
    rows = fetch_stock_frgn_data("005930")
"""

import logging
import re
from dataclasses import dataclass
from datetime import date

import httpx
from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

NAVER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}


@dataclass
class StockFrgnRow:
    """종목별 외국인/기관 일별 수급 데이터."""

    trade_date: date
    close_price: int  # 종가 (원)
    inst_net_volume: int  # 기관 순매매량 (주)
    frgn_net_volume: int  # 외국인 순매매량 (주)
    frgn_holding_ratio: float  # 외국인 보유율 (%)


def _parse_signed_int(text: str) -> int:
    """부호+콤마 포함된 숫자 문자열을 int로 변환.

    예: "+1,554,880" → 1554880, "-19,602,376" → -19602376
    """
    cleaned = text.replace(",", "").replace("+", "")
    # 유니코드 마이너스 정규화
    cleaned = cleaned.replace("−", "-").replace("–", "-")
    if not cleaned or cleaned == "-":
        return 0
    return int(cleaned)


def _parse_int(text: str) -> int:
    """콤마 포함된 양수 문자열을 int로 변환."""
    cleaned = text.replace(",", "")
    if not cleaned or cleaned == "-":
        return 0
    return int(cleaned)


def _parse_float(text: str) -> float:
    """퍼센트 문자열을 float로 변환. 예: "50.33%" → 50.33"""
    cleaned = text.replace(",", "").replace("%", "")
    cleaned = cleaned.replace("−", "-").replace("–", "-")
    if not cleaned or cleaned == "-":
        return 0.0
    return float(cleaned)


def parse_frgn_table(html: str) -> list[StockFrgnRow]:
    """frgn.naver HTML에서 외국인/기관 수급 테이블 파싱.

    Args:
        html: UTF-8 디코딩된 HTML 문자열

    Returns:
        StockFrgnRow 리스트 (최신순)
    """
    soup = BeautifulSoup(html, "html.parser")

    # 두 번째 table.type2 = "외국인 기관 순매매 거래량" 테이블
    tables = soup.select("table.type2")
    target: Tag | None = None
    for t in tables:
        summary = t.get("summary", "")
        if isinstance(summary, str) and "외국인" in summary:
            target = t
            break

    if target is None:
        return []

    rows: list[StockFrgnRow] = []
    for tr in target.select("tr"):
        tds = tr.select("td")
        if len(tds) < 9:
            continue

        date_text = tds[0].get_text(strip=True)
        # 날짜 형식: "2026.02.27"
        if not re.match(r"\d{4}\.\d{2}\.\d{2}", date_text):
            continue

        try:
            trade_date = date.fromisoformat(date_text.replace(".", "-"))
            close_price = _parse_int(tds[1].get_text(strip=True))
            inst_net_volume = _parse_signed_int(tds[5].get_text(strip=True))
            frgn_net_volume = _parse_signed_int(tds[6].get_text(strip=True))
            frgn_holding_ratio = _parse_float(tds[8].get_text(strip=True))
        except (ValueError, IndexError) as e:
            logger.debug("Row parse error (%s): %s", date_text, e)
            continue

        rows.append(
            StockFrgnRow(
                trade_date=trade_date,
                close_price=close_price,
                inst_net_volume=inst_net_volume,
                frgn_net_volume=frgn_net_volume,
                frgn_holding_ratio=frgn_holding_ratio,
            )
        )

    return rows


def fetch_stock_frgn_data(stock_code: str) -> list[StockFrgnRow] | None:
    """네이버 금융 종목별 외국인/기관 수급 데이터 조회.

    Args:
        stock_code: 6자리 종목코드 (e.g. "005930")

    Returns:
        StockFrgnRow 리스트 (최신순, ~20거래일) or None on failure.
    """
    url = f"https://finance.naver.com/item/frgn.naver?code={stock_code}&page=1"
    try:
        resp = httpx.get(url, headers=NAVER_HEADERS, timeout=10)
        resp.encoding = "euc-kr"
        html = resp.text

        rows = parse_frgn_table(html)
        if not rows:
            logger.warning("[%s] No frgn data rows parsed", stock_code)
            return None

        return rows
    except Exception as e:
        logger.warning("[%s] Naver frgn fetch failed: %s", stock_code, e)
        return None
