"""Claude Provider — Anthropic Claude API (Haiku, Sonnet, Opus)."""

import json
import logging
import re
from typing import Any

from prime_jennie.infra.llm.base import BaseLLMProvider, LLMResponse
from prime_jennie.infra.llm.factory import register_provider

logger = logging.getLogger(__name__)


class ClaudeLLMProvider(BaseLLMProvider):
    """Anthropic Claude API Provider."""

    def __init__(self) -> None:
        import anthropic

        from prime_jennie.domain.config import get_config

        config = get_config()
        api_key = config.secrets.anthropic_api_key
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._default_model = config.llm.claude_model

    @property
    def provider_name(self) -> str:
        return "claude"

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

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system

        response = await self._client.messages.create(**kwargs)

        content = response.content[0].text if response.content else ""
        usage = response.usage
        tokens_in = usage.input_tokens if usage else 0
        tokens_out = usage.output_tokens if usage else 0

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
        max_tokens: int = 4096,
        service: str | None = None,
    ) -> dict[str, Any]:
        sys_msg = system or "You are a helpful assistant. Always respond with valid JSON only, no markdown formatting."

        response = await self.generate(
            prompt,
            system=sys_msg,
            temperature=temperature,
            max_tokens=max_tokens,
            service=service,
        )

        content = response.content.strip()

        # Markdown 블록 제거
        m = re.search(r"```(?:json)?\s*(.*?)```", content, re.DOTALL)
        if m:
            content = m.group(1).strip()

        return json.loads(content)

    async def generate_json_with_thinking(
        self,
        prompt: str,
        schema: dict[str, Any],
        *,
        model: str = "claude-opus-4-6-20250219",
        budget_tokens: int = 8000,
        max_tokens: int = 16000,
        service: str | None = None,
    ) -> dict[str, Any]:
        """Extended Thinking을 활용한 JSON 생성 (Opus 전용)."""
        response = await self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            thinking={
                "type": "enabled",
                "budget_tokens": budget_tokens,
            },
            messages=[{"role": "user", "content": prompt}],
        )

        # 토큰 사용량 기록
        usage = response.usage
        llm_resp = LLMResponse(
            content="",
            model=model,
            tokens_in=usage.input_tokens if usage else 0,
            tokens_out=usage.output_tokens if usage else 0,
            provider=self.provider_name,
        )
        self._record_usage(llm_resp, service)

        # TextBlock에서 content 추출 (ThinkingBlock 스킵)
        from anthropic.types import TextBlock

        text_content = ""
        for block in response.content:
            if isinstance(block, TextBlock):
                text_content = block.text
                break

        if not text_content:
            raise ValueError("No TextBlock in Claude Extended Thinking response")

        # JSON 추출
        m = re.search(r"```(?:json)?\s*(.*?)```", text_content, re.DOTALL)
        if m:
            text_content = m.group(1).strip()

        return json.loads(text_content)

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError("Claude does not support embeddings")


# 팩토리 자동 등록
register_provider("claude", ClaudeLLMProvider)
