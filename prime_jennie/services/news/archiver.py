"""News Archiver — Redis Stream → Qdrant 벡터 DB 저장.

뉴스를 임베딩하여 벡터 DB에 저장 (RAG 검색용).
LLM 호출 없음 — 순수 임베딩 + 저장.
"""

import logging

import redis

logger = logging.getLogger(__name__)

NEWS_STREAM = "stream:news:raw"
ARCHIVER_GROUP = "group_archiver"
ARCHIVER_CONSUMER = "archiver_1"
BLOCK_MS = 2000
BATCH_SIZE = 20


class NewsArchiver:
    """뉴스 벡터 DB 저장기.

    Args:
        redis_client: Redis 클라이언트
        qdrant_url: Qdrant 서버 URL
        embed_url: vLLM 임베딩 서버 URL
        collection_name: Qdrant 컬렉션명
    """

    def __init__(
        self,
        redis_client: redis.Redis,
        qdrant_url: str = "http://localhost:6333",
        embed_url: str = "http://localhost:8002/v1",
        collection_name: str = "rag_stock_data",
    ):
        self._redis = redis_client
        self._qdrant_url = qdrant_url
        self._embed_url = embed_url
        self._collection_name = collection_name
        self._vectorstore = None
        self._ensure_consumer_group()

    def _ensure_consumer_group(self) -> None:
        """Consumer group 생성 (없으면)."""
        try:
            self._redis.xgroup_create(NEWS_STREAM, ARCHIVER_GROUP, id="0", mkstream=True)
        except redis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

    def _init_vectorstore(self):
        """Qdrant + LangChain 벡터스토어 지연 초기화."""
        if self._vectorstore is not None:
            return

        try:
            from langchain_openai import OpenAIEmbeddings
            from langchain_qdrant import QdrantVectorStore
            from qdrant_client import QdrantClient

            embeddings = OpenAIEmbeddings(
                model="nlpai-lab/KURE-v1",
                openai_api_base=self._embed_url,
                openai_api_key="not-needed",
            )

            client = QdrantClient(url=self._qdrant_url)
            self._vectorstore = QdrantVectorStore(
                client=client,
                collection_name=self._collection_name,
                embedding=embeddings,
            )
            logger.info("Qdrant vectorstore initialized: %s", self._collection_name)
        except ImportError:
            logger.error("langchain/qdrant packages not installed")
        except Exception as e:
            logger.error("Qdrant init failed: %s", e)

    def run_once(self, max_messages: int = 1000) -> int:
        """한 번 실행. 처리한 메시지 수 반환."""
        self._init_vectorstore()
        if not self._vectorstore:
            return 0

        processed = 0

        # Pending 복구
        processed += self._process_pending()

        # 신규 메시지
        while processed < max_messages:
            messages = self._redis.xreadgroup(
                ARCHIVER_GROUP,
                ARCHIVER_CONSUMER,
                {NEWS_STREAM: ">"},
                count=BATCH_SIZE,
                block=BLOCK_MS,
            )
            if not messages:
                break

            for _stream_name, entries in messages:
                for msg_id, data in entries:
                    try:
                        self._archive_message(data)
                    except Exception:
                        logger.warning("Archive failed for %s", msg_id)
                    finally:
                        self._redis.xack(NEWS_STREAM, ARCHIVER_GROUP, msg_id)
                        processed += 1

        return processed

    def _process_pending(self) -> int:
        """미ACK 메시지 복구."""
        count = 0
        while True:
            pending = self._redis.xreadgroup(
                ARCHIVER_GROUP,
                ARCHIVER_CONSUMER,
                {NEWS_STREAM: "0"},
                count=BATCH_SIZE,
            )
            if not pending:
                break

            has_messages = False
            for _stream_name, entries in pending:
                if not entries:
                    continue
                has_messages = True
                for msg_id, data in entries:
                    try:
                        self._archive_message(data)
                    except Exception:
                        pass
                    finally:
                        self._redis.xack(NEWS_STREAM, ARCHIVER_GROUP, msg_id)
                        count += 1

            if not has_messages:
                break

        return count

    def _archive_message(self, data: dict) -> None:
        """단일 뉴스 벡터 DB 저장."""
        from langchain_core.documents import Document
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        headline = (
            data.get(b"headline", data.get("headline", b"")).decode()
            if isinstance(data.get(b"headline", data.get("headline", "")), bytes)
            else data.get("headline", "")
        )

        stock_code = (
            data.get(b"stock_code", data.get("stock_code", b"")).decode()
            if isinstance(data.get(b"stock_code", data.get("stock_code", "")), bytes)
            else data.get("stock_code", "")
        )

        article_url = (
            data.get(b"article_url", data.get("article_url", b"")).decode()
            if isinstance(data.get(b"article_url", data.get("article_url", "")), bytes)
            else data.get("article_url", "")
        )

        if not headline:
            return

        content = f"[{stock_code}] {headline}"
        metadata = {
            "stock_code": stock_code,
            "source_url": article_url,
            "source": data.get("source", "NAVER")
            if isinstance(data.get("source"), str)
            else data.get(b"source", b"NAVER").decode()
            if isinstance(data.get(b"source"), bytes)
            else "NAVER",
        }

        doc = Document(page_content=content, metadata=metadata)
        splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
        chunks = splitter.split_documents([doc])
        self._vectorstore.add_documents(chunks)
