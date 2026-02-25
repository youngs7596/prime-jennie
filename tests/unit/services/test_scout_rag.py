"""Scout RAG Retriever 단위 테스트."""

from unittest.mock import MagicMock, patch

from prime_jennie.domain.enums import SectorGroup
from prime_jennie.domain.macro import TradingContext
from prime_jennie.domain.scoring import QuantScore
from prime_jennie.domain.stock import StockMaster
from prime_jennie.services.scout.enrichment import EnrichedCandidate

# ─── Fixtures ────────────────────────────────────────────────────


def _make_master(code: str = "005930", name: str = "삼성전자", sector=SectorGroup.SEMICONDUCTOR_IT):
    return StockMaster(
        stock_code=code,
        stock_name=name,
        market="KOSPI",
        market_cap=400_000_000_000_000,
        sector_group=sector,
    )


def _make_enriched(code: str = "005930", name: str = "삼성전자", sector=SectorGroup.SEMICONDUCTOR_IT):
    return EnrichedCandidate(master=_make_master(code, name, sector))


def _make_doc(content: str, stock_code: str = "005930", created_at_utc: int = 9999999999):
    """Mock LangChain Document."""
    doc = MagicMock()
    doc.page_content = content
    doc.metadata = {"stock_code": stock_code, "created_at_utc": created_at_utc}
    return doc


# ─── init_vectorstore ────────────────────────────────────────────


def test_init_vectorstore_disabled():
    """enable_news_analysis=False → None 반환."""
    with patch("prime_jennie.services.scout.rag_retriever.get_config") as mock_config:
        mock_config.return_value.scout.enable_news_analysis = False
        from prime_jennie.services.scout.rag_retriever import init_vectorstore

        result = init_vectorstore()
        assert result is None


def test_init_vectorstore_import_error():
    """langchain 미설치 → None 반환 (파이프라인 중단 없음)."""
    import builtins

    original_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if "langchain_qdrant" in name:
            raise ImportError("No module named 'langchain_qdrant'")
        return original_import(name, *args, **kwargs)

    with (
        patch("prime_jennie.services.scout.rag_retriever.get_config") as mock_config,
        patch("builtins.__import__", side_effect=mock_import),
    ):
        mock_config.return_value.scout.enable_news_analysis = True
        from prime_jennie.services.scout.rag_retriever import init_vectorstore

        result = init_vectorstore()
        assert result is None


# ─── discover_rag_candidates ─────────────────────────────────────


def test_discover_rag_candidates_returns_new_stocks():
    """RAG 후보 발굴: 기존 universe에 없는 종목 반환."""
    mock_vs = MagicMock()

    # 4개 토픽 쿼리에 대해 각각 다른 결과 반환
    mock_vs.similarity_search.side_effect = [
        [_make_doc("[000660] SK하이닉스 실적 개선", stock_code="000660")],
        [_make_doc("[035420] NAVER 수주", stock_code="035420")],
        [_make_doc("[005930] 삼성전자 M&A", stock_code="005930")],  # 기존 universe에 있음
        [],
    ]

    existing = {"005930": _make_master()}

    from prime_jennie.services.scout.rag_retriever import discover_rag_candidates

    result = discover_rag_candidates(mock_vs, existing)

    assert "000660" in result
    assert "035420" in result
    assert "005930" not in result  # 기존 universe 종목은 제외
    assert "실적 개선" in result["000660"]


def test_discover_rag_candidates_none_vectorstore():
    """vectorstore=None → 빈 dict."""
    from prime_jennie.services.scout.rag_retriever import discover_rag_candidates

    result = discover_rag_candidates(None, {})
    assert result == {}


# ─── fetch_news_for_stocks ───────────────────────────────────────


def test_fetch_news_for_stocks_returns_formatted_text():
    """종목별 뉴스 프리페치: 포맷된 텍스트 반환."""
    mock_vs = MagicMock()

    docs = [
        _make_doc("[005930] 삼성전자 반도체 실적 호전", stock_code="005930"),
        _make_doc("[005930] 삼성전자 HBM 수주 확대", stock_code="005930"),
    ]

    # 3 base queries + 1 sector query = 4 calls
    mock_vs.similarity_search.side_effect = [
        docs[:1],
        docs[1:],
        [],
        [],  # sector query
    ]

    enriched = {"005930": _make_enriched()}

    from prime_jennie.services.scout.rag_retriever import fetch_news_for_stocks

    result = fetch_news_for_stocks(mock_vs, enriched, max_workers=1)

    assert "005930" in result
    assert "삼성전자" in result["005930"]
    assert "|" in result["005930"]  # 복수 뉴스 구분자


def test_fetch_news_for_stocks_no_news():
    """뉴스 없는 종목 → '최근 관련 뉴스 없음'."""
    mock_vs = MagicMock()
    mock_vs.similarity_search.return_value = []

    enriched = {"005930": _make_enriched()}

    from prime_jennie.services.scout.rag_retriever import fetch_news_for_stocks

    result = fetch_news_for_stocks(mock_vs, enriched, max_workers=1)

    assert result["005930"] == "최근 관련 뉴스 없음"


def test_fetch_news_for_stocks_none_vectorstore():
    """vectorstore=None → 빈 dict."""
    from prime_jennie.services.scout.rag_retriever import fetch_news_for_stocks

    result = fetch_news_for_stocks(None, {})
    assert result == {}


# ─── analyst 프롬프트 뉴스 주입 ───────────────────────────────────


def test_news_context_in_prompt():
    """rag_news_context → _build_prompt에 포함."""
    from prime_jennie.services.scout.analyst import _build_prompt

    candidate = _make_enriched()
    candidate.rag_news_context = "[삼성전자 HBM 수주 확대] | [반도체 업황 개선]"

    quant = QuantScore(
        stock_code="005930",
        stock_name="삼성전자",
        total_score=71.0,
        momentum_score=15.0,
        quality_score=14.0,
        value_score=12.0,
        technical_score=7.0,
        news_score=8.0,
        supply_demand_score=15.0,
    )
    context = TradingContext.default()

    prompt = _build_prompt(quant, candidate, context)
    assert "### 최근 뉴스 (RAG)" in prompt
    assert "삼성전자 HBM 수주 확대" in prompt


def test_news_context_skipped_when_empty():
    """rag_news_context=None → 뉴스 섹션 미포함."""
    from prime_jennie.services.scout.analyst import _build_prompt

    candidate = _make_enriched()
    # rag_news_context is None by default

    quant = QuantScore(
        stock_code="005930",
        stock_name="삼성전자",
        total_score=71.0,
        momentum_score=15.0,
        quality_score=14.0,
        value_score=12.0,
        technical_score=7.0,
        news_score=8.0,
        supply_demand_score=15.0,
    )
    context = TradingContext.default()

    prompt = _build_prompt(quant, candidate, context)
    assert "### 최근 뉴스 (RAG)" not in prompt


def test_news_context_skipped_for_placeholder():
    """'뉴스 DB 미연결' 등 플레이스홀더 → 뉴스 섹션 미포함."""
    from prime_jennie.services.scout.analyst import _build_prompt

    candidate = _make_enriched()
    candidate.rag_news_context = "뉴스 DB 미연결"

    quant = QuantScore(
        stock_code="005930",
        stock_name="삼성전자",
        total_score=71.0,
        momentum_score=15.0,
        quality_score=14.0,
        value_score=12.0,
        technical_score=7.0,
        news_score=8.0,
        supply_demand_score=15.0,
    )
    context = TradingContext.default()

    prompt = _build_prompt(quant, candidate, context)
    assert "### 최근 뉴스 (RAG)" not in prompt
