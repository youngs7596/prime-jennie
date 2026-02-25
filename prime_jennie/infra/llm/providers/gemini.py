"""Gemini Provider — Google Gemini API."""

import json
import logging
from typing import Any

from prime_jennie.infra.llm.base import BaseLLMProvider, LLMResponse
from prime_jennie.infra.llm.factory import register_provider

logger = logging.getLogger(__name__)


class GeminiLLMProvider(BaseLLMProvider):
    """Google Gemini API Provider."""

    def __init__(self) -> None:
        from google import genai

        from prime_jennie.domain.config import get_config

        config = get_config()
        api_key = config.secrets.gemini_api_key
        self._client = genai.Client(api_key=api_key)
        self._default_model = config.llm.gemini_model

    @property
    def provider_name(self) -> str:
        return "gemini"

    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        service: str | None = None,
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

        resp = LLMResponse(
            content=content,
            model=self._default_model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            provider=self.provider_name,
        )
        self._record_usage(resp, service)
        return resp

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

        # 토큰 사용량 기록
        usage = response.usage_metadata
        llm_resp = LLMResponse(
            content=content,
            model=self._default_model,
            tokens_in=usage.prompt_token_count if usage else 0,
            tokens_out=usage.candidates_token_count if usage else 0,
            provider=self.provider_name,
        )
        self._record_usage(llm_resp, service)

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
