"""LLM Provider 인터페이스 — 모든 provider가 구현해야 하는 계약."""

import logging
from abc import ABC, abstractmethod
from typing import Any, Optional

from pydantic import BaseModel

_logger = logging.getLogger(__name__)


class LLMResponse(BaseModel):
    """LLM 응답 표준 형식."""

    content: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    provider: str = ""


class BaseLLMProvider(ABC):
    """LLM Provider 추상 클래스.

    모든 provider (Ollama/vLLM, OpenAI, Claude, Gemini, CloudFailover)가 구현.
    """

    def _record_usage(self, response: LLMResponse, service: Optional[str]) -> None:
        """LLM 사용량을 Redis에 기록 (모든 provider 공통)."""
        if response.tokens_in == 0 and response.tokens_out == 0:
            return
        try:
            from prime_jennie.infra.redis.client import get_redis
            from prime_jennie.infra.observability.metrics import record_llm_usage

            record_llm_usage(
                get_redis(),
                service=service or "unknown",
                tokens_in=response.tokens_in,
                tokens_out=response.tokens_out,
                model=response.model,
            )
        except Exception:
            _logger.debug("LLM usage recording failed", exc_info=True)

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        service: Optional[str] = None,
    ) -> LLMResponse:
        """텍스트 생성."""
        ...

    @abstractmethod
    async def generate_json(
        self,
        prompt: str,
        schema: dict[str, Any],
        *,
        system: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        service: Optional[str] = None,
    ) -> dict[str, Any]:
        """JSON 구조화 출력 생성.

        Args:
            schema: JSON Schema (Pydantic model.model_json_schema() 또는 수동 스키마)

        Returns:
            파싱된 JSON dict. 스키마 불일치 시 ValueError.
        """
        ...

    @abstractmethod
    async def generate_embeddings(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        """텍스트 임베딩 생성."""
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Provider 식별자 (로깅/통계용)."""
        ...
