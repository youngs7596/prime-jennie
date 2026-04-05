"""US Market crawler unit tests."""

from datetime import date
from unittest.mock import patch

from prime_jennie.infra.crawlers.us_market import (
    US_TICKERS,
    USMarketDaily,
    fetch_us_daily,
    fetch_us_market_batch,
)

# Yahoo Finance API 응답 mock
MOCK_YAHOO_RESPONSE = {
    "chart": {
        "result": [
            {
                "timestamp": [1712188800, 1712275200, 1712361600],  # 3일치
                "indicators": {
                    "quote": [
                        {
                            "open": [100.0, 102.0, 101.0],
                            "high": [103.0, 105.0, 104.0],
                            "low": [99.0, 101.0, 100.0],
                            "close": [102.0, 104.0, 103.0],
                            "volume": [1000000, 1200000, 1100000],
                        }
                    ]
                },
            }
        ]
    }
}


class MockResponse:
    status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return MOCK_YAHOO_RESPONSE


class TestFetchUsDaily:
    @patch("prime_jennie.infra.crawlers.us_market.httpx.get", return_value=MockResponse())
    def test_returns_list_of_dataclass(self, mock_get):
        rows = fetch_us_daily("^SOX", days=30)
        assert len(rows) == 3
        assert all(isinstance(r, USMarketDaily) for r in rows)

    @patch("prime_jennie.infra.crawlers.us_market.httpx.get", return_value=MockResponse())
    def test_ticker_name_mapped(self, mock_get):
        rows = fetch_us_daily("^SOX", days=30)
        assert rows[0].ticker == "SOX"

    @patch("prime_jennie.infra.crawlers.us_market.httpx.get", return_value=MockResponse())
    def test_change_pct_calculated(self, mock_get):
        rows = fetch_us_daily("^SOX", days=30)
        # 첫 번째는 전일 데이터 없어서 None
        assert rows[0].change_pct is None
        # 두 번째: (104 - 102) / 102 * 100 ≈ 1.9608
        assert rows[1].change_pct is not None
        assert abs(rows[1].change_pct - 1.9608) < 0.01

    @patch("prime_jennie.infra.crawlers.us_market.httpx.get", return_value=MockResponse())
    def test_price_date_is_date(self, mock_get):
        rows = fetch_us_daily("NVDA", days=30)
        assert all(isinstance(r.price_date, date) for r in rows)

    @patch("prime_jennie.infra.crawlers.us_market.httpx.get", side_effect=Exception("timeout"))
    def test_returns_empty_on_error(self, mock_get):
        rows = fetch_us_daily("^SOX", days=30)
        assert rows == []

    @patch("prime_jennie.infra.crawlers.us_market.httpx.get", return_value=MockResponse())
    def test_yahoo_range_mapping(self, mock_get):
        """days에 따라 올바른 Yahoo range 파라미터 전달."""
        fetch_us_daily("^SOX", days=30)
        call_args = mock_get.call_args
        assert call_args.kwargs["params"]["range"] == "1mo"

        fetch_us_daily("^SOX", days=365)
        call_args = mock_get.call_args
        assert call_args.kwargs["params"]["range"] == "1y"

        fetch_us_daily("^SOX", days=500)
        call_args = mock_get.call_args
        assert call_args.kwargs["params"]["range"] == "2y"


class TestFetchUsMarketBatch:
    @patch("prime_jennie.infra.crawlers.us_market.httpx.get", return_value=MockResponse())
    def test_returns_all_tickers(self, mock_get):
        result = fetch_us_market_batch(days=30)
        assert len(result) == len(US_TICKERS)
        for ticker_name in US_TICKERS.values():
            assert ticker_name in result

    @patch("prime_jennie.infra.crawlers.us_market.httpx.get", return_value=MockResponse())
    def test_each_ticker_has_rows(self, mock_get):
        result = fetch_us_market_batch(days=30)
        for rows in result.values():
            assert len(rows) > 0


class TestNoneHandling:
    """Yahoo API가 일부 None 값을 반환하는 경우."""

    @patch("prime_jennie.infra.crawlers.us_market.httpx.get")
    def test_skips_none_close(self, mock_get):
        response_with_none = {
            "chart": {
                "result": [
                    {
                        "timestamp": [1712188800, 1712275200],
                        "indicators": {
                            "quote": [
                                {
                                    "open": [100.0, None],
                                    "high": [103.0, None],
                                    "low": [99.0, None],
                                    "close": [102.0, None],  # 두 번째 None
                                    "volume": [1000000, None],
                                }
                            ]
                        },
                    }
                ]
            }
        }

        class MockResp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return response_with_none

        mock_get.return_value = MockResp()
        rows = fetch_us_daily("^SOX", days=30)
        assert len(rows) == 1  # None close는 스킵
