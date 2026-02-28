"""컨센서스 크롤러 — FnGuide + 네이버 금융.

Forward PER/EPS/ROE, 목표주가, 애널리스트 수 수집.
양쪽 모두 구현 → 성공한 쪽 반환 (FnGuide 우선).

Usage:
    data = crawl_consensus("005930")  # 삼성전자
"""

import logging
import re
from dataclasses import dataclass

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

MIN_ANALYST_COUNT = 3  # thin coverage 필터


@dataclass
class ConsensusData:
    """크롤링 결과 — forward 컨센서스."""

    forward_per: float | None = None
    forward_eps: float | None = None
    forward_roe: float | None = None
    target_price: int | None = None
    analyst_count: int | None = None
    investment_opinion: float | None = None
    source: str = "FNGUIDE"


def crawl_consensus(stock_code: str) -> ConsensusData | None:
    """FnGuide → Naver 순서로 시도, 성공한 쪽 반환.

    analyst_count < 3이면 None (thin coverage 필터).
    """
    # FnGuide 우선
    result = crawl_fnguide_consensus(stock_code)
    source = "FNGUIDE"

    # FnGuide 실패 시 Naver 시도
    if result is None:
        result = crawl_naver_consensus(stock_code)
        source = "NAVER"

    if result is None:
        return None

    result.source = source

    # thin coverage 필터: 애널리스트 3명 미만이면 신뢰도 낮음
    if result.analyst_count is not None and result.analyst_count < MIN_ANALYST_COUNT:
        logger.debug("[%s] Thin coverage (%d analysts), skipping", stock_code, result.analyst_count)
        return None

    return result


def crawl_fnguide_consensus(stock_code: str) -> ConsensusData | None:
    """FnGuide 컨센서스 크롤링.

    URL: comp.fnguide.com/SVO2/ASP/SVD_Main.asp?gicode=A{code}
    주요 파싱 대상: 컨센서스 테이블 (Forward PER, EPS, ROE, 목표주가).
    """
    url = f"https://comp.fnguide.com/SVO2/ASP/SVD_Main.asp?gicode=A{stock_code}"
    try:
        resp = httpx.get(url, headers=_HEADERS, timeout=15, follow_redirects=True)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")

        result = ConsensusData()

        # FnGuide 메인 페이지: "컨센서스" 또는 "Consensus" 섹션 파싱
        # div#svdMainGrid 내 "컨센서스" 테이블에 Forward PER/EPS 등이 있음
        _parse_fnguide_consensus_table(soup, result)

        # 목표주가 + 애널리스트 수 파싱
        _parse_fnguide_target_price(soup, result)

        # 유효 데이터 있는지 확인
        if result.forward_per is None and result.forward_eps is None:
            return None

        return result

    except Exception as e:
        logger.debug("[%s] FnGuide consensus crawl failed: %s", stock_code, e)
        return None


def _parse_fnguide_consensus_table(soup: BeautifulSoup, result: ConsensusData) -> None:
    """FnGuide 메인 페이지에서 컨센서스 데이터 파싱.

    "컨센서스" 또는 "투자의견" 섹션의 테이블에서 Forward PER, EPS 추출.
    """
    # 방법 1: div#svdMainGrid 내 테이블 — "EPS(원)" 행 찾기
    for table in soup.select("table"):
        rows = table.select("tr")
        for row in rows:
            th = row.select_one("th, td.cmp-table-cell")
            if not th:
                continue
            label = th.get_text(strip=True)

            tds = row.select("td")
            if not tds:
                continue

            # EPS(원) — 컨센서스 Forward EPS (first match wins)
            if "EPS" in label and "원" in label and result.forward_eps is None:
                val = _parse_number(tds[-1].get_text(strip=True))
                if val is not None:
                    result.forward_eps = val

            # PER(배) — 컨센서스 Forward PER (first match wins)
            if "PER" in label and "배" in label and result.forward_per is None:
                val = _parse_number(tds[-1].get_text(strip=True))
                if val is not None and val > 0:
                    result.forward_per = val

            # ROE(%) — 컨센서스 Forward ROE (first match wins)
            if "ROE" in label and result.forward_roe is None:
                val = _parse_number(tds[-1].get_text(strip=True))
                if val is not None:
                    result.forward_roe = val

    # 방법 2: snap_all 클래스의 테이블 (FnGuide 공통 레이아웃)
    for div in soup.select("div.corp_group2, div.corp_group1"):
        for table in div.select("table"):
            for row in table.select("tr"):
                ths = row.select("th")
                tds = row.select("td")
                if not ths or not tds:
                    continue

                for th, td in zip(ths, tds, strict=False):
                    label = th.get_text(strip=True)
                    val_text = td.get_text(strip=True)

                    if "PER" in label and result.forward_per is None:
                        val = _parse_number(val_text)
                        if val is not None and val > 0:
                            result.forward_per = val

                    if "EPS" in label and result.forward_eps is None:
                        val = _parse_number(val_text)
                        if val is not None:
                            result.forward_eps = val

                    if "ROE" in label and result.forward_roe is None:
                        val = _parse_number(val_text)
                        if val is not None:
                            result.forward_roe = val


def _parse_fnguide_target_price(soup: BeautifulSoup, result: ConsensusData) -> None:
    """FnGuide 목표주가 + 투자의견 + 애널리스트 수."""
    for table in soup.select("table"):
        for row in table.select("tr"):
            th = row.select_one("th")
            if not th:
                continue
            label = th.get_text(strip=True)
            tds = row.select("td")
            if not tds:
                continue

            if "목표주가" in label or "Target" in label:
                val = _parse_number(tds[-1].get_text(strip=True))
                if val is not None and val > 0:
                    result.target_price = int(val)

            if "투자의견" in label or "컨센서스" in label:
                val = _parse_number(tds[-1].get_text(strip=True))
                if val is not None and 1 <= val <= 5:
                    result.investment_opinion = val

            if "애널리스트" in label or "커버" in label:
                val = _parse_number(tds[-1].get_text(strip=True))
                if val is not None and val > 0:
                    result.analyst_count = int(val)


def crawl_naver_consensus(stock_code: str) -> ConsensusData | None:
    """네이버 컨센서스 크롤링.

    URL: navercomp.wisereport.co.kr/v2/company/c1010001.aspx?cmp_cd={code}
    투자의견 컨센서스 테이블에서 Forward PER/EPS/ROE 파싱.
    """
    url = f"https://navercomp.wisereport.co.kr/v2/company/c1010001.aspx?cmp_cd={stock_code}"
    try:
        resp = httpx.get(url, headers=_HEADERS, timeout=15, follow_redirects=True)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")

        result = ConsensusData()

        # 네이버 컨센서스 페이지 파싱
        _parse_naver_consensus_page(soup, result)

        # 유효 데이터 있는지 확인
        if result.forward_per is None and result.forward_eps is None:
            return None

        return result

    except Exception as e:
        logger.debug("[%s] Naver consensus crawl failed: %s", stock_code, e)
        return None


def _parse_naver_consensus_page(soup: BeautifulSoup, result: ConsensusData) -> None:
    """네이버 컨센서스 페이지 파싱.

    wisereport 페이지의 투자의견/실적 테이블에서 데이터 추출.
    """
    # 컨센서스 테이블 파싱 — dt/dd 또는 table 내 구조
    for table in soup.select("table"):
        rows = table.select("tr")
        for row in rows:
            ths = row.select("th")
            tds = row.select("td")
            if not ths or not tds:
                continue

            for th in ths:
                label = th.get_text(strip=True)

                # 마지막 유효 td에서 forward 값 추출 (가장 최근 추정치)
                for td in reversed(tds):
                    val_text = td.get_text(strip=True)
                    if not val_text or val_text in ("-", "N/A", ""):
                        continue
                    # 수식/설명 텍스트 필터링 (한글, 괄호 등이 포함되면 숫자가 아님)
                    if any(c in val_text for c in ("주주", "자본", "순이익", "당기")):
                        continue
                    val = _parse_number(val_text)
                    if val is None:
                        continue

                    if "EPS" in label and result.forward_eps is None:
                        result.forward_eps = val
                    elif "PER" in label and result.forward_per is None:
                        if val > 0:
                            result.forward_per = val
                    elif "ROE" in label and result.forward_roe is None:
                        result.forward_roe = val
                    break  # 첫 유효 값만 사용

    # 목표주가, 투자의견, 애널리스트 수
    for dl in soup.select("dl, div.cmp_comment"):
        text = dl.get_text()
        if "목표주가" in text:
            val = _extract_number_after(text, "목표주가")
            if val and val > 0:
                result.target_price = int(val)
        if "투자의견" in text:
            val = _extract_number_after(text, "투자의견")
            if val and 1 <= val <= 5:
                result.investment_opinion = val

    # 애널리스트 수: "n명" 패턴
    analyst_patterns = soup.find_all(string=re.compile(r"\d+명"))
    for pat in analyst_patterns:
        parent = pat.find_parent()
        if parent and ("애널리스트" in parent.get_text() or "기관" in parent.get_text()):
            match = re.search(r"(\d+)명", pat)
            if match:
                result.analyst_count = int(match.group(1))
                break


def _parse_number(text: str) -> float | None:
    """숫자 파싱 (쉼표 제거, +/- 부호 허용)."""
    if not text:
        return None
    cleaned = text.replace(",", "").replace(" ", "").strip()
    # 부호 + 숫자 + 소수점 패턴
    match = re.search(r"[+-]?\d+\.?\d*", cleaned)
    if match:
        try:
            return float(match.group())
        except ValueError:
            return None
    return None


def _extract_number_after(text: str, keyword: str) -> float | None:
    """텍스트에서 키워드 뒤의 첫 번째 숫자 추출."""
    idx = text.find(keyword)
    if idx < 0:
        return None
    after = text[idx + len(keyword) :]
    return _parse_number(after)
