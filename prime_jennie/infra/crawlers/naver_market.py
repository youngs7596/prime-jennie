"""네이버 금융 시장 데이터 크롤러 — KOSPI/KOSDAQ 지수 + 투자자 수급.

pykrx (data.krx.co.kr) 장애 대체용.
KRX Open API 키 도착 시 krx_market.py 로 재전환 예정.

Usage:
    idx = fetch_index_data("KOSPI")
    flows = fetch_investor_flows("kospi", "20260227")
"""

import logging
from dataclasses import dataclass
from datetime import date

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

NAVER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}


@dataclass
class IndexData:
    """시장 지수 데이터."""

    close: float
    change_pct: float
    traded_at: date


@dataclass
class InvestorFlows:
    """투자자별 순매수 (억원)."""

    foreign_net: float
    institutional_net: float
    retail_net: float
    trade_date: date


def fetch_index_data(index_code: str) -> IndexData | None:
    """네이버 모바일 API에서 KOSPI/KOSDAQ 지수 조회.

    Args:
        index_code: "KOSPI" 또는 "KOSDAQ"

    Returns:
        IndexData or None on failure.
    """
    url = f"https://m.stock.naver.com/api/index/{index_code}/basic"
    try:
        resp = httpx.get(url, headers=NAVER_HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        close = float(data["closePrice"].replace(",", ""))
        change_pct = float(data["fluctuationsRatio"])
        traded_at_str = data["localTradedAt"][:10]  # "2026-02-27T..."
        traded_at = date.fromisoformat(traded_at_str)

        return IndexData(close=close, change_pct=change_pct, traded_at=traded_at)
    except Exception as e:
        logger.warning("Naver index fetch failed (%s): %s", index_code, e)
        return None


def fetch_investor_flows(market: str, bizdate: str) -> InvestorFlows | None:
    """네이버 금융 투자자별 매매동향 조회.

    Args:
        market: "kospi" 또는 "kosdaq" (소문자)
        bizdate: "YYYYMMDD" 형식

    Returns:
        InvestorFlows or None on failure.
    """
    # sosession 파라미터: "01"=코스피, "02"=코스닥
    sosession = "01" if market.lower() == "kospi" else "02"
    url = "https://finance.naver.com/sise/investorDealTrendDay.naver"
    params = {"bizdate": bizdate, "sosession": sosession}

    try:
        resp = httpx.get(url, headers=NAVER_HEADERS, params=params, timeout=10)
        resp.encoding = "euc-kr"
        soup = BeautifulSoup(resp.text, "html.parser")

        table = soup.select_one("table.type_1")
        if not table:
            logger.warning("Naver investor table not found for %s", market)
            return None

        # 헤더에서 컬럼 인덱스 매핑
        header_row = table.select_one("tr")
        if not header_row:
            return None

        headers = [th.get_text(strip=True) for th in header_row.select("th")]
        col_map: dict[str, int] = {}
        for i, h in enumerate(headers):
            if "외국인" in h:
                col_map["foreign"] = i
            elif "기관" in h:
                col_map["institutional"] = i
            elif "개인" in h:
                col_map["retail"] = i

        if not col_map:
            logger.warning("Naver investor header parsing failed for %s", market)
            return None

        # bizdate 뒤 6자리와 매칭하는 행 찾기 (날짜: "26.02.27")
        target_short = bizdate[2:]  # "YYYYMMDD" → "YYMMDD"
        rows = table.select("tr")[1:]  # 헤더 제외

        def _parse_val(tds: list, idx: int) -> float:
            if idx >= len(tds):
                return 0.0
            # td index는 헤더 기준 - 날짜 컬럼(0) 빼기
            td_idx = idx - 1  # th 첫번째가 날짜, td 첫번째도 날짜
            if td_idx < 0 or td_idx >= len(tds):
                return 0.0
            raw = tds[td_idx].get_text(strip=True).replace(",", "")
            # 음수 부호 처리 (네이버는 마이너스를 다양한 방식으로 표기)
            raw = raw.replace("−", "-").replace("–", "-")
            if not raw or raw == "-":
                return 0.0
            try:
                return float(raw)
            except ValueError:
                return 0.0

        for row in rows:
            tds = row.select("td")
            if not tds:
                continue

            date_text = tds[0].get_text(strip=True)
            # "26.02.27" → "260227"
            row_date = date_text.replace(".", "")
            if row_date != target_short:
                continue

            foreign_net = _parse_val(tds, col_map.get("foreign", 0))
            institutional_net = _parse_val(tds, col_map.get("institutional", 0))
            retail_net = _parse_val(tds, col_map.get("retail", 0))

            trade_date = (
                date(
                    int("20" + bizdate[2:4]),
                    int(bizdate[4:6]),
                    int(bizdate[6:8]),
                )
                if len(bizdate) == 8
                else date.fromisoformat(bizdate)
            )

            return InvestorFlows(
                foreign_net=foreign_net,
                institutional_net=institutional_net,
                retail_net=retail_net,
                trade_date=trade_date,
            )

        logger.warning("Naver investor: no row for date %s (%s)", bizdate, market)
        return None

    except Exception as e:
        logger.warning("Naver investor flows fetch failed (%s): %s", market, e)
        return None
