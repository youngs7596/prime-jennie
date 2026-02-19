"""CloudFailover Provider — DeepSeek multi-chain failover.

Provider 체인 (순차 시도):
  1. DeepSeek Official API (deepseek-chat)
  2. OpenRouter (deepseek/deepseek-v3.2)
  3. 로컬 vLLM (Ollama Provider)
"""

import logging
import os
from typing import Any, Optional

from prime_jennie.infra.llm.base import BaseLLMProvider, LLMResponse
from prime_jennie.infra.llm.factory import register_provider

logger = logging.getLogger(__name__)


class CloudFailoverProvider(BaseLLMProvider):
    """Sequential failover across multiple OpenAI-compatible providers."""

    def __init__(self) -> None:
        self._providers: list[tuple[str, BaseLLMProvider]] = []
        self._init_chain()

        if not self._providers:
            raise RuntimeError(
                "CloudFailoverProvider: No providers available. "
                "Set DEEPSEEK_API_KEY or OPENROUTER_API_KEY."
            )

        names = [name for name, _ in self._providers]
        logger.info("CloudFailover chain: %s", " → ".join(names))

    def _init_chain(self) -> None:
        """Provider 체인 초기화 (API key 존재하는 것만)."""
        from .openai_provider import OpenAILLMProvider

        # 1. DeepSeek Official API
        ds_key = os.getenv("DEEPSEEK_API_KEY", "")
        if ds_key:
            provider = OpenAILLMProvider(
                api_key=ds_key,
                base_url="https://api.deepseek.com",
                default_model="deepseek-chat",
            )
            self._providers.append(("deepseek-api", provider))

        # 2. OpenRouter
        or_key = os.getenv("OPENROUTER_API_KEY", "")
        if or_key:
            provider = OpenAILLMProvider(
                api_key=or_key,
                base_url="https://openrouter.ai/api/v1",
                default_model="deepseek/deepseek-v3.2",
            )
            self._providers.append(("openrouter", provider))

        # 3. Local vLLM fallback
        try:
            from .ollama import OllamaLLMProvider

            self._providers.append(("local-vllm", OllamaLLMProvider()))
        except Exception:
            pass  # vLLM 미실행 시 스킵

    @property
    def provider_name(self) -> str:
        return "deepseek_cloud"

    async def _failover_call(self, method: str, **kwargs: Any) -> Any:
        """순차 failover 호출."""
        errors = []
        for name, provider in self._providers:
            try:
                result = await getattr(provider, method)(**kwargs)
                if len(errors) > 0:
                    logger.info(
                        "CloudFailover: %s succeeded (after %d failures)",
                        name,
                        len(errors),
                    )
                return result
            except Exception as e:
                logger.warning("CloudFailover: %s failed: %s", name, e)
                errors.append((name, e))

        raise RuntimeError(
            f"All CloudFailover providers failed: "
            + ", ".join(f"{n}: {e}" for n, e in errors)
        )

    async def generate(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        service: Optional[str] = None,
    ) -> LLMResponse:
        return await self._failover_call(
            "generate",
            prompt=prompt,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
            service=service,
        )

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
        return await self._failover_call(
            "generate_json",
            prompt=prompt,
            schema=schema,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
            service=service,
        )

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        return await self._failover_call("generate_embeddings", texts=texts)


# 팩토리 자동 등록
register_provider("deepseek_cloud", CloudFailoverProvider)
