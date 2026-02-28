"""네이버 금융 시장 데이터 크롤러 — KOSPI/KOSDAQ 지수 + 투자자 수급 + 종목 목록.

pykrx (data.krx.co.kr) 장애 대체용.
KRX Open API 키 도착 시 krx_market.py 로 재전환 예정.

Usage:
    idx = fetch_index_data("KOSPI")
    flows = fetch_investor_flows("kospi", "20260227")
    stocks = fetch_market_stocks("KOSPI")
    ohlcv = fetch_index_daily_prices("KOSPI", count=250)
"""

import logging
import time
import xml.etree.ElementTree as ET
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
            # th와 td 인덱스가 1:1 대응 (th[0]=날짜, td[0]=날짜)
            raw = tds[idx].get_text(strip=True).replace(",", "")
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


@dataclass
class MarketStock:
    """시가총액 순위 페이지에서 파싱한 종목 정보."""

    stock_code: str
    stock_name: str
    market_cap: int  # 백만원 단위 (DB 컨벤션)


def fetch_market_stocks(market: str = "KOSPI") -> list[MarketStock]:
    """네이버 시가총액 순위 페이지에서 전 종목 코드/이름/시총 크롤링.

    Args:
        market: "KOSPI" 또는 "KOSDAQ"

    Returns:
        MarketStock 리스트 (시총 내림차순)
    """
    # sosok: 0=코스피, 1=코스닥
    sosok = "0" if market.upper() == "KOSPI" else "1"
    url = "https://finance.naver.com/sise/sise_market_sum.naver"
    stocks: list[MarketStock] = []
    seen: set[str] = set()

    for page in range(1, 100):  # 최대 100페이지 안전 장치
        try:
            resp = httpx.get(
                url,
                headers=NAVER_HEADERS,
                params={"sosok": sosok, "page": str(page)},
                timeout=10,
            )
            resp.encoding = "euc-kr"
            soup = BeautifulSoup(resp.text, "html.parser")

            table = soup.select_one("table.type_2")
            if not table:
                break

            page_count = 0
            for tr in table.select("tr"):
                tds = tr.select("td")
                if len(tds) < 7:
                    continue

                link = tr.select_one("a[href*='code=']")
                if not link:
                    continue

                href = link.get("href", "")
                code = href.split("code=")[-1].split("&")[0]
                if len(code) != 6 or not code.isdigit():
                    continue
                if code in seen:
                    continue

                name = link.get_text(strip=True)
                if not name:
                    continue

                # 시가총액: tds[6], 억원 단위 → 백만원 (×100)
                cap_text = tds[6].get_text(strip=True).replace(",", "")
                if not cap_text or cap_text == "-":
                    continue
                try:
                    cap_eok = int(cap_text)
                except ValueError:
                    continue

                seen.add(code)
                stocks.append(
                    MarketStock(
                        stock_code=code,
                        stock_name=name,
                        market_cap=cap_eok * 100,  # 억원 → 백만원
                    )
                )
                page_count += 1

            if page_count == 0:
                break  # 데이터 없는 페이지 → 마지막

            if page < 99:
                time.sleep(0.15)

        except Exception as e:
            logger.warning("Naver market stocks page %d failed: %s", page, e)
            break

    logger.info("Naver market stocks (%s): %d stocks fetched", market, len(stocks))
    return stocks


# ─── fchart XML API: 지수 일봉 OHLCV ────────────────────────────


# fchart API 지수 코드 매핑 (네이버 내부 코드)
_FCHART_INDEX_CODE = {
    "KOSPI": "KOSPI",
    "KOSDAQ": "KOSDAQ",
}


@dataclass
class IndexDailyOHLCV:
    """지수 일봉 OHLCV."""

    index_code: str
    price_date: date
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: int


def fetch_index_daily_prices(index_code: str, count: int = 250) -> list[IndexDailyOHLCV]:
    """fchart API에서 지수 일봉 OHLCV 조회.

    Args:
        index_code: "KOSPI" 또는 "KOSDAQ"
        count: 조회 거래일 수 (기본 250 ≈ 1년)

    Returns:
        IndexDailyOHLCV 리스트 (오래된 순 정렬). 에러 시 빈 리스트.
    """
    fchart_code = _FCHART_INDEX_CODE.get(index_code.upper(), index_code.upper())
    url = "https://fchart.stock.naver.com/sise.nhn"
    params = {
        "symbol": fchart_code,
        "timeframe": "day",
        "count": str(count),
        "requestType": "0",
    }

    try:
        resp = httpx.get(url, headers=NAVER_HEADERS, params=params, timeout=15)
        resp.raise_for_status()

        root = ET.fromstring(resp.text)
        items: list[IndexDailyOHLCV] = []

        for item in root.iter("item"):
            data = item.get("data", "")
            parts = data.split("|")
            if len(parts) < 6:
                continue

            try:
                price_date = date(
                    int(parts[0][:4]),
                    int(parts[0][4:6]),
                    int(parts[0][6:8]),
                )
                items.append(
                    IndexDailyOHLCV(
                        index_code=index_code.upper(),
                        price_date=price_date,
                        open_price=float(parts[1]),
                        high_price=float(parts[2]),
                        low_price=float(parts[3]),
                        close_price=float(parts[4]),
                        volume=int(parts[5]),
                    )
                )
            except (ValueError, IndexError) as e:
                logger.debug("fchart item parse skip: %s — %s", data, e)
                continue

        # 오래된 순 정렬
        items.sort(key=lambda x: x.price_date)
        logger.info("fchart %s: %d daily bars fetched", index_code, len(items))
        return items

    except Exception as e:
        logger.warning("fchart index fetch failed (%s): %s", index_code, e)
        return []
