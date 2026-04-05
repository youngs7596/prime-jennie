"""미국 시장 데이터 크롤러 — Yahoo Finance API 기반.

SOX(필라델피아 반도체), NVDA, S&P 500, 나스닥 100 선물 등
미국 주요 지표 일봉 데이터를 수집.

Usage:
    rows = fetch_us_daily("^SOX", days=500)
    batch = fetch_us_market_batch(days=500)
"""

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime

import httpx

logger = logging.getLogger(__name__)

# 수집 대상 티커 목록
US_TICKERS: dict[str, str] = {
    "^SOX": "SOX",  # 필라델피아 반도체 지수
    "NVDA": "NVDA",  # 엔비디아
    "^GSPC": "SP500",  # S&P 500
    "^IXIC": "NASDAQ",  # 나스닥 종합
    "NQ=F": "NQ_FUT",  # 나스닥 100 선물
}

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
YAHOO_HEADERS = {"User-Agent": "Mozilla/5.0"}


@dataclass
class USMarketDaily:
    """미국 시장 일봉 데이터."""

    ticker: str  # 정규화된 이름 (SOX, NVDA, SP500, ...)
    price_date: date
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: int
    change_pct: float | None = None  # 전일 대비 %


def fetch_us_daily(yahoo_ticker: str, days: int = 500) -> list[USMarketDaily]:
    """Yahoo Finance API로 단일 티커 일봉 데이터 조회.

    Args:
        yahoo_ticker: Yahoo Finance 티커 (예: "^SOX", "NVDA")
        days: 조회 기간 (거래일 기준, 최대 2년)

    Returns:
        날짜 오름차순 정렬된 일봉 리스트
    """
    ticker_name = US_TICKERS.get(yahoo_ticker, yahoo_ticker)

    # days → Yahoo range 매핑
    if days <= 30:
        yrange = "1mo"
    elif days <= 90:
        yrange = "3mo"
    elif days <= 180:
        yrange = "6mo"
    elif days <= 365:
        yrange = "1y"
    else:
        yrange = "2y"

    try:
        resp = httpx.get(
            YAHOO_CHART_URL.format(ticker=yahoo_ticker),
            params={"range": yrange, "interval": "1d"},
            headers=YAHOO_HEADERS,
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()

        result_data = data["chart"]["result"][0]
        timestamps = result_data["timestamp"]
        quote = result_data["indicators"]["quote"][0]

        opens = quote["open"]
        highs = quote["high"]
        lows = quote["low"]
        closes = quote["close"]
        volumes = quote["volume"]

        rows: list[USMarketDaily] = []
        prev_close: float | None = None

        for i, ts in enumerate(timestamps):
            o, h, lo, c, v = opens[i], highs[i], lows[i], closes[i], volumes[i]
            if c is None:
                continue

            price_date = datetime.fromtimestamp(ts, tz=UTC).date()

            change_pct = None
            if prev_close and prev_close > 0:
                change_pct = round((c - prev_close) / prev_close * 100, 4)

            rows.append(
                USMarketDaily(
                    ticker=ticker_name,
                    price_date=price_date,
                    open_price=round(o, 2) if o else 0.0,
                    high_price=round(h, 2) if h else 0.0,
                    low_price=round(lo, 2) if lo else 0.0,
                    close_price=round(c, 2),
                    volume=int(v) if v else 0,
                    change_pct=change_pct,
                )
            )
            prev_close = c

        logger.info("Yahoo %s: %d daily rows fetched (range=%s)", ticker_name, len(rows), yrange)
        return rows

    except Exception as e:
        logger.warning("Yahoo %s fetch failed: %s", ticker_name, e)
        return []


def fetch_us_market_batch(days: int = 500) -> dict[str, list[USMarketDaily]]:
    """US_TICKERS 전체를 순차 수집.

    Returns:
        {ticker_name: [USMarketDaily, ...]} 딕셔너리
    """
    result: dict[str, list[USMarketDaily]] = {}
    for yahoo_ticker, ticker_name in US_TICKERS.items():
        rows = fetch_us_daily(yahoo_ticker, days=days)
        if rows:
            result[ticker_name] = rows
    return result
