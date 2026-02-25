"""OpenAI Provider — GPT-4o, DeepSeek API 등 OpenAI-compatible API."""

import json
import logging
import os
import re
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
        "deepseek-reasoner",
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
        sys_msg = system or "You are a helpful assistant."
        # DeepSeek 등 일부 API는 json_object response_format 사용 시
        # 프롬프트에 "json" 단어가 반드시 포함되어야 함
        if "json" not in sys_msg.lower():
            sys_msg += " Always respond with valid JSON."

        # 스키마를 프롬프트에 포함 (DeepSeek 등 json_schema 미지원 모델용)
        schema_instruction = ""
        if schema:
            schema_instruction = (
                f"\n\nYou MUST respond with a JSON object that follows this exact schema:\n"
                f"```json\n{json.dumps(schema, ensure_ascii=False, indent=2)}\n```\n"
                f"Use exactly the field names shown above. Do not use different key names."
            )

        messages = [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": prompt + schema_instruction},
        ]

        is_reasoning = self._is_reasoning_model(model)

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        # Reasoning 모델 (deepseek-reasoner 등)은 response_format: json_object 미지원
        if not is_reasoning:
            kwargs["response_format"] = {"type": "json_object"}
            kwargs["temperature"] = temperature

        raw = await self._client.chat.completions.create(**kwargs)

        content = raw.choices[0].message.content or ""
        if not content.strip():
            raise ValueError("LLM returned empty content")

        # 토큰 사용량 기록
        usage = raw.usage
        llm_resp = LLMResponse(
            content=content,
            model=model,
            tokens_in=usage.prompt_tokens if usage else 0,
            tokens_out=usage.completion_tokens if usage else 0,
            provider=self.provider_name,
        )
        self._record_usage(llm_resp, service)

        return _extract_json(content)

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        response = await self._client.embeddings.create(model="text-embedding-3-small", input=texts)
        return [item.embedding for item in response.data]


def _extract_json(text: str) -> dict[str, Any]:
    """LLM 응답에서 JSON 객체 추출 (```json ... ``` 블록 또는 raw JSON)."""
    # 1. ```json ... ``` 코드 블록
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if m:
        return json.loads(m.group(1).strip())
    # 2. raw JSON (첫 번째 { ... } 블록)
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        return json.loads(m.group(0))
    return json.loads(text)


# 팩토리 자동 등록
register_provider("openai", OpenAILLMProvider)
