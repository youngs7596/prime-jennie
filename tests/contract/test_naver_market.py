"""Contract smoke tests — 네이버 시장 데이터 크롤러.

KOSPI/KOSDAQ 지수 및 투자자 수급 데이터 엔드포인트가
정상 응답하는지 live 테스트.

실행: pytest tests/contract/test_naver_market.py -v
CI:   주간 cron (schedule) 트리거에서만 실행.
"""

from datetime import date, timedelta

import pytest

from prime_jennie.infra.crawlers.naver_market import (
    IndexDailyOHLCV,
    IndexData,
    InvestorFlows,
    fetch_index_daily_prices,
    fetch_index_data,
    fetch_investor_flows,
)

# ── fixtures (같은 엔드포인트 중복 요청 방지) ──────────────────────


@pytest.fixture(scope="module")
def kospi_index() -> IndexData | None:
    return fetch_index_data("KOSPI")


@pytest.fixture(scope="module")
def kosdaq_index() -> IndexData | None:
    return fetch_index_data("KOSDAQ")


@pytest.fixture(scope="module")
def kospi_flows() -> InvestorFlows | None:
    # 최근 7일 이내 거래일 탐색
    today = date.today()
    for days_ago in range(7):
        d = today - timedelta(days=days_ago)
        bizdate = d.strftime("%Y%m%d")
        result = fetch_investor_flows("kospi", bizdate)
        if result is not None:
            return result
    return None


# ── KOSPI Index ──────────────────────────────────────────────────


class TestKospiIndex:
    def test_returns_data(self, kospi_index):
        assert kospi_index is not None, "fetch_index_data('KOSPI') returned None"

    def test_close_range(self, kospi_index):
        if kospi_index is None:
            pytest.skip("no data")
        assert 1000 <= kospi_index.close <= 10000, f"KOSPI close out of range: {kospi_index.close}"

    def test_change_pct_range(self, kospi_index):
        if kospi_index is None:
            pytest.skip("no data")
        assert -15 <= kospi_index.change_pct <= 15, f"KOSPI change_pct out of range: {kospi_index.change_pct}"

    def test_traded_at_is_date(self, kospi_index):
        if kospi_index is None:
            pytest.skip("no data")
        assert isinstance(kospi_index.traded_at, date)


# ── KOSDAQ Index ─────────────────────────────────────────────────


class TestKosdaqIndex:
    def test_returns_data(self, kosdaq_index):
        assert kosdaq_index is not None, "fetch_index_data('KOSDAQ') returned None"

    def test_close_range(self, kosdaq_index):
        if kosdaq_index is None:
            pytest.skip("no data")
        assert 300 <= kosdaq_index.close <= 2000, f"KOSDAQ close out of range: {kosdaq_index.close}"

    def test_change_pct_range(self, kosdaq_index):
        if kosdaq_index is None:
            pytest.skip("no data")
        assert -15 <= kosdaq_index.change_pct <= 15, f"KOSDAQ change_pct out of range: {kosdaq_index.change_pct}"


# ── Investor Flows ───────────────────────────────────────────────


class TestInvestorFlows:
    def test_returns_data(self, kospi_flows):
        assert kospi_flows is not None, "fetch_investor_flows returned None (7일 내 거래일 없음?)"

    def test_foreign_net_type(self, kospi_flows):
        if kospi_flows is None:
            pytest.skip("no data")
        assert isinstance(kospi_flows.foreign_net, float)
        assert -100_000 <= kospi_flows.foreign_net <= 100_000, f"foreign_net out of range: {kospi_flows.foreign_net}"

    def test_institutional_net_type(self, kospi_flows):
        if kospi_flows is None:
            pytest.skip("no data")
        assert isinstance(kospi_flows.institutional_net, float)
        assert -100_000 <= kospi_flows.institutional_net <= 100_000

    def test_retail_net_type(self, kospi_flows):
        if kospi_flows is None:
            pytest.skip("no data")
        assert isinstance(kospi_flows.retail_net, float)
        assert -100_000 <= kospi_flows.retail_net <= 100_000

    def test_trade_date_is_date(self, kospi_flows):
        if kospi_flows is None:
            pytest.skip("no data")
        assert isinstance(kospi_flows.trade_date, date)


# ── Index Daily OHLCV (fchart) ──────────────────────────────────


@pytest.fixture(scope="module")
def kospi_daily() -> list[IndexDailyOHLCV]:
    return fetch_index_daily_prices("KOSPI", count=30)


@pytest.fixture(scope="module")
def kosdaq_daily() -> list[IndexDailyOHLCV]:
    return fetch_index_daily_prices("KOSDAQ", count=30)


class TestIndexDailyPrices:
    def test_kospi_returns_data(self, kospi_daily):
        assert len(kospi_daily) > 0, "fetch_index_daily_prices('KOSPI') returned empty"

    def test_kosdaq_returns_data(self, kosdaq_daily):
        assert len(kosdaq_daily) > 0, "fetch_index_daily_prices('KOSDAQ') returned empty"

    def test_kospi_ohlcv_range(self, kospi_daily):
        if not kospi_daily:
            pytest.skip("no data")
        for row in kospi_daily:
            assert 1000 <= row.close_price <= 10000, f"KOSPI close out of range: {row.close_price}"
            assert row.low_price <= row.high_price
            assert row.volume >= 0

    def test_kosdaq_ohlcv_range(self, kosdaq_daily):
        if not kosdaq_daily:
            pytest.skip("no data")
        for row in kosdaq_daily:
            assert 300 <= row.close_price <= 2000, f"KOSDAQ close out of range: {row.close_price}"
            assert row.low_price <= row.high_price
            assert row.volume >= 0

    def test_date_order_ascending(self, kospi_daily):
        if len(kospi_daily) < 2:
            pytest.skip("not enough data")
        dates = [r.price_date for r in kospi_daily]
        assert dates == sorted(dates), "Dates not in ascending order"

    def test_index_code_set(self, kospi_daily):
        if not kospi_daily:
            pytest.skip("no data")
        assert all(r.index_code == "KOSPI" for r in kospi_daily)
