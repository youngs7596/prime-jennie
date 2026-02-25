"""Scout RAG Retriever — Qdrant 벡터 DB 뉴스 검색.

3가지 기능:
  1. init_vectorstore(): Qdrant 벡터스토어 초기화
  2. discover_rag_candidates(): RAG 기반 후보 발굴 (4개 토픽)
  3. fetch_news_for_stocks(): 종목별 뉴스 프리페치 (LLM 프롬프트용)
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from prime_jennie.domain.config import get_config
from prime_jennie.domain.enums import SectorGroup

from .enrichment import EnrichedCandidate

logger = logging.getLogger(__name__)

# 토픽 쿼리: (검색 쿼리, reason_tag)
_TOPIC_QUERIES = [
    ("실적 개선 매출 성장 영업이익 흑자전환", "실적 개선"),
    ("신규 수주 대규모 계약 공급 체결", "수주/계약"),
    ("신사업 진출 인수합병 전략적 투자", "신사업/M&A"),
    ("배당 증가 자사주 매입 주주환원", "주주환원"),
]

# 섹터별 추가 쿼리
_SECTOR_QUERIES: dict[SectorGroup, str] = {
    SectorGroup.SEMICONDUCTOR_IT: "반도체 AI 서버 HBM 수요",
    SectorGroup.BIO_HEALTH: "임상 승인 FDA 신약 파이프라인",
    SectorGroup.SECONDARY_BATTERY: "2차전지 양극재 음극재 전해질 수주",
    SectorGroup.AUTOMOBILE: "전기차 자율주행 수출 판매",
    SectorGroup.CONSTRUCTION: "분양 수주 SOC 인프라",
    SectorGroup.CHEMICAL: "정유 석유화학 스프레드",
    SectorGroup.DEFENSE_SHIPBUILDING: "방산 수출 조선 수주 LNG선",
    SectorGroup.MEDIA_ENTERTAINMENT: "콘텐츠 IP 플랫폼 흥행",
    SectorGroup.STEEL_MATERIAL: "철강 수요 원자재 가격",
}


def init_vectorstore():
    """Qdrant 벡터스토어 초기화. 비활성/실패 시 None 반환."""
    config = get_config()
    if not config.scout.enable_news_analysis:
        logger.info("RAG news analysis disabled (SCOUT_ENABLE_NEWS_ANALYSIS=false)")
        return None

    try:
        from langchain_openai import OpenAIEmbeddings
        from langchain_qdrant import QdrantVectorStore
        from qdrant_client import QdrantClient

        embeddings = OpenAIEmbeddings(
            model="nlpai-lab/KURE-v1",
            openai_api_base=config.llm.vllm_embed_url,
            openai_api_key="not-needed",
        )
        client = QdrantClient(url="http://localhost:6333")
        vs = QdrantVectorStore(
            client=client,
            collection_name="rag_stock_data",
            embedding=embeddings,
        )
        logger.info("RAG vectorstore initialized for scout")
        return vs
    except ImportError:
        logger.warning("RAG dependencies not installed (langchain-qdrant), skipping")
        return None
    except Exception as e:
        logger.warning("RAG vectorstore init failed: %s, skipping", e)
        return None


def discover_rag_candidates(
    vectorstore,
    existing: dict[str, object],
) -> dict[str, list[str]]:
    """RAG 기반 후보 발굴 — 4개 토픽 쿼리로 유망 종목 탐색.

    Args:
        vectorstore: QdrantVectorStore (None이면 빈 dict)
        existing: 기존 universe {stock_code: ...}

    Returns:
        {stock_code: [reason_tags]} — 신규 발견 종목
    """
    if vectorstore is None:
        return {}

    cutoff_ts = int((datetime.now(UTC) - timedelta(days=7)).timestamp())
    discovered: dict[str, list[str]] = {}

    try:
        from qdrant_client.models import FieldCondition, Filter, Range
    except ImportError:
        return {}

    for query_text, tag in _TOPIC_QUERIES:
        try:
            time_filter = Filter(
                must=[
                    FieldCondition(
                        key="metadata.created_at_utc",
                        range=Range(gte=cutoff_ts),
                    )
                ]
            )
            docs = vectorstore.similarity_search(
                query=query_text,
                k=20,
                filter=time_filter,
            )

            for doc in docs:
                code = doc.metadata.get("stock_code", "")
                if code and code not in existing:
                    discovered.setdefault(code, [])
                    if tag not in discovered[code]:
                        discovered[code].append(tag)
        except Exception as e:
            logger.warning("RAG discover query failed (%s): %s", tag, e)

    logger.info("RAG discovered %d new candidates", len(discovered))
    return discovered


def fetch_news_for_stocks(
    vectorstore,
    enriched: dict[str, EnrichedCandidate],
    max_workers: int = 8,
) -> dict[str, str]:
    """종목별 뉴스 프리페치 — LLM 프롬프트 주입용.

    Args:
        vectorstore: QdrantVectorStore (None이면 빈 dict)
        enriched: {stock_code: EnrichedCandidate}
        max_workers: 병렬 워커 수

    Returns:
        {stock_code: "뉴스 요약 텍스트"} — 최대 5건, 150자 truncate
    """
    if vectorstore is None:
        return {}

    try:
        from qdrant_client.models import FieldCondition, Filter, MatchValue, Range
    except ImportError:
        return {}

    cutoff_ts = int((datetime.now(UTC) - timedelta(days=7)).timestamp())
    result: dict[str, str] = {}

    def _fetch_one(code: str, candidate: EnrichedCandidate) -> tuple[str, str]:
        name = candidate.master.stock_name
        sector = candidate.master.sector_group

        # 다중 쿼리 조합
        queries = [
            f"{name} 실적 매출 영업이익",
            f"{name} 신규 수주 계약 사업",
            f"{name} 리스크 하락 우려 손실",
        ]

        # 섹터별 추가 쿼리
        if sector and sector in _SECTOR_QUERIES:
            queries.append(f"{name} {_SECTOR_QUERIES[sector]}")

        # 종목 + 시간 필터
        stock_time_filter = Filter(
            must=[
                FieldCondition(
                    key="metadata.stock_code",
                    match=MatchValue(value=code),
                ),
                FieldCondition(
                    key="metadata.created_at_utc",
                    range=Range(gte=cutoff_ts),
                ),
            ]
        )

        seen: set[str] = set()
        all_docs = []

        for q in queries:
            try:
                docs = vectorstore.similarity_search(
                    query=q,
                    k=3,
                    filter=stock_time_filter,
                )
                for doc in docs:
                    key = doc.page_content[:80]
                    if key not in seen:
                        seen.add(key)
                        all_docs.append(doc)
            except Exception:
                pass

        # Fallback: 필터 없이 종목명으로 검색
        if not all_docs:
            try:
                docs = vectorstore.similarity_search(
                    query=f"{name} 주식 뉴스",
                    k=5,
                )
                for doc in docs:
                    code_in_meta = doc.metadata.get("stock_code", "")
                    if code_in_meta == code:
                        key = doc.page_content[:80]
                        if key not in seen:
                            seen.add(key)
                            all_docs.append(doc)
            except Exception:
                pass

        if not all_docs:
            return code, "최근 관련 뉴스 없음"

        # 최대 5건, 150자 truncate
        snippets = []
        for doc in all_docs[:5]:
            text = doc.page_content.strip()
            if len(text) > 150:
                text = text[:147] + "..."
            snippets.append(f"[{text}]")

        return code, " | ".join(snippets)

    start = time.monotonic()

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_fetch_one, code, cand) for code, cand in enriched.items()]
        for future in futures:
            try:
                code, text = future.result(timeout=30)
                result[code] = text
            except Exception as e:
                logger.warning("RAG news fetch failed: %s", e)

    elapsed = time.monotonic() - start
    news_found = sum(1 for v in result.values() if v != "최근 관련 뉴스 없음")
    logger.info(
        "RAG news prefetch: %d/%d stocks with news (%.1fs)",
        news_found,
        len(enriched),
        elapsed,
    )
    return result
