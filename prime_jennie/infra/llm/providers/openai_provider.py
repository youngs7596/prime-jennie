"""OpenAI Provider — GPT-4o, DeepSeek API 등 OpenAI-compatible API."""

import json
import logging
import os
from typing import Any

from prime_jennie.infra.llm.base import BaseLLMProvider, LLMResponse
from prime_jennie.infra.llm.factory import register_provider

logger = logging.getLogger(__name__)

# Reasoning 모델 (temperature 미지원)
REASONING_MODELS = frozenset(
    {
        "o1",
        "o1-mini",
        "o1-preview",
        "o3",
        "o3-mini",
        "gpt-5-mini",
        "gpt-5",
        "gpt-5.2",
    }
)


class OpenAILLMProvider(BaseLLMProvider):
    """OpenAI API Provider (GPT-4o, DeepSeek, etc.)."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        default_model: str | None = None,
    ) -> None:
        import openai

        from prime_jennie.domain.config import get_config

        config = get_config()
        self._api_key = api_key or config.secrets.openai_api_key
        self._base_url = base_url or os.getenv("OPENAI_API_BASE")
        self._default_model = default_model or config.llm.openai_model

        client_kwargs: dict[str, Any] = {"api_key": self._api_key}
        if self._base_url:
            client_kwargs["base_url"] = self._base_url

        self._client = openai.AsyncOpenAI(**client_kwargs)

    @property
    def provider_name(self) -> str:
        return "openai"

    def _is_reasoning_model(self, model: str) -> bool:
        return any(rm in model.lower() for rm in REASONING_MODELS)

    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        service: str | None = None,
    ) -> LLMResponse:
        model = self._default_model
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if not self._is_reasoning_model(model):
            kwargs["temperature"] = temperature

        response = await self._client.chat.completions.create(**kwargs)

        content = response.choices[0].message.content or ""
        usage = response.usage
        tokens_in = usage.prompt_tokens if usage else 0
        tokens_out = usage.completion_tokens if usage else 0

        response = LLMResponse(
            content=content,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            provider=self.provider_name,
        )
        self._record_usage(response, service)
        return response

    async def generate_json(
        self,
        prompt: str,
        schema: dict[str, Any],
        *,
        system: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        service: str | None = None,
    ) -> dict[str, Any]:
        model = self._default_model
        sys_msg = system or "You are a helpful assistant. Always respond with valid JSON."

        messages = [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": prompt},
        ]

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        if not self._is_reasoning_model(model):
            kwargs["temperature"] = temperature

        response = await self._client.chat.completions.create(**kwargs)

        content = response.choices[0].message.content or ""
        return json.loads(content)

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        response = await self._client.embeddings.create(model="text-embedding-3-small", input=texts)
        return [item.embedding for item in response.data]


# 팩토리 자동 등록
register_provider("openai", OpenAILLMProvider)
