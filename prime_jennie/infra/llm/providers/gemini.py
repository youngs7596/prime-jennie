"""Gemini Provider — Google Gemini API."""

import json
import logging
import os
from typing import Any, Optional

from prime_jennie.infra.llm.base import BaseLLMProvider, LLMResponse
from prime_jennie.infra.llm.factory import register_provider

logger = logging.getLogger(__name__)


class GeminiLLMProvider(BaseLLMProvider):
    """Google Gemini API Provider."""

    def __init__(self) -> None:
        from google import genai

        api_key = os.getenv("GEMINI_API_KEY", "")
        self._client = genai.Client(api_key=api_key)
        self._default_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    @property
    def provider_name(self) -> str:
        return "gemini"

    async def generate(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        service: Optional[str] = None,
    ) -> LLMResponse:
        from google.genai import types

        config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
        )
        if system:
            config.system_instruction = system

        response = await self._client.aio.models.generate_content(
            model=self._default_model,
            contents=prompt,
            config=config,
        )

        content = response.text or ""
        usage = response.usage_metadata
        tokens_in = usage.prompt_token_count if usage else 0
        tokens_out = usage.candidates_token_count if usage else 0

        return LLMResponse(
            content=content,
            model=self._default_model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            provider=self.provider_name,
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
        from google.genai import types

        config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
            response_mime_type="application/json",
        )
        if system:
            config.system_instruction = system

        response = await self._client.aio.models.generate_content(
            model=self._default_model,
            contents=prompt,
            config=config,
        )

        content = response.text or ""
        return json.loads(content)

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        results = []
        for text in texts:
            response = await self._client.aio.models.embed_content(
                model="text-embedding-004",
                contents=text,
            )
            results.append(response.embeddings[0].values)
        return results


# 팩토리 자동 등록
register_provider("gemini", GeminiLLMProvider)
