"""Contract smoke tests — 네이버 종목별 외국인/기관 수급 크롤러.

삼성전자(005930) sentinel로 live 엔드포인트 검증.

실행: pytest tests/contract/test_naver_stock.py -v
CI:   주간 cron (schedule) 트리거에서만 실행.
"""

from datetime import date

import pytest

from prime_jennie.infra.crawlers.naver_stock import StockFrgnRow, fetch_stock_frgn_data


@pytest.fixture(scope="module")
def samsung_frgn() -> list[StockFrgnRow] | None:
    return fetch_stock_frgn_data("005930")


class TestFetchStockFrgnData:
    def test_returns_list(self, samsung_frgn):
        assert samsung_frgn is not None, "fetch_stock_frgn_data('005930') returned None"
        assert isinstance(samsung_frgn, list)
        assert len(samsung_frgn) >= 1

    def test_row_type(self, samsung_frgn):
        if samsung_frgn is None:
            pytest.skip("no data")
        assert isinstance(samsung_frgn[0], StockFrgnRow)

    def test_trade_date_is_date(self, samsung_frgn):
        if samsung_frgn is None:
            pytest.skip("no data")
        for row in samsung_frgn:
            assert isinstance(row.trade_date, date)

    def test_close_price_positive(self, samsung_frgn):
        if samsung_frgn is None:
            pytest.skip("no data")
        for row in samsung_frgn:
            assert row.close_price > 0, f"close_price must be positive: {row.close_price}"

    def test_holding_ratio_range(self, samsung_frgn):
        if samsung_frgn is None:
            pytest.skip("no data")
        for row in samsung_frgn:
            assert 0 <= row.frgn_holding_ratio <= 100, f"ratio out of range: {row.frgn_holding_ratio}"

    def test_volume_types(self, samsung_frgn):
        if samsung_frgn is None:
            pytest.skip("no data")
        row = samsung_frgn[0]
        assert isinstance(row.inst_net_volume, int)
        assert isinstance(row.frgn_net_volume, int)

    def test_sufficient_rows(self, samsung_frgn):
        """page=1은 ~20거래일 데이터 → 7일 집계에 충분."""
        if samsung_frgn is None:
            pytest.skip("no data")
        assert len(samsung_frgn) >= 7, f"Expected >= 7 rows, got {len(samsung_frgn)}"
