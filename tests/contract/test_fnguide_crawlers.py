"""Contract smoke tests — FnGuide 크롤러.

Sentinel 종목(삼성전자 005930)으로 실제 HTTP 요청을 보내
HTML 구조 변경 및 파싱 오류를 감지한다.

실행: pytest tests/contract/test_fnguide_crawlers.py -v
CI:   주간 cron (schedule) 트리거에서만 실행.
"""

import pytest

from prime_jennie.infra.crawlers.fnguide import ConsensusData, crawl_fnguide_consensus

SENTINEL_CODE = "005930"


@pytest.fixture(scope="module")
def consensus() -> ConsensusData | None:
    return crawl_fnguide_consensus(SENTINEL_CODE)


class TestFnGuideConsensus:
    def test_returns_data(self, consensus):
        assert consensus is not None, "crawl_fnguide_consensus returned None"

    def test_forward_per_range(self, consensus):
        if consensus is None:
            pytest.skip("no data")
        assert consensus.forward_per is not None, "forward_per is None"
        assert 1 < consensus.forward_per < 200, f"forward_per out of range: {consensus.forward_per}"

    def test_forward_eps_exists(self, consensus):
        if consensus is None:
            pytest.skip("no data")
        assert consensus.forward_eps is not None, "forward_eps is None"

    def test_target_price_exists(self, consensus):
        if consensus is None:
            pytest.skip("no data")
        if consensus.target_price is None:
            pytest.skip("target_price not available")
        assert consensus.target_price > 0, f"target_price invalid: {consensus.target_price}"
