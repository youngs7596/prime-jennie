"""Ollama/vLLM Provider — 로컬 LLM (EXAONE 4.0 32B AWQ).

vLLM OpenAI-compatible API를 직접 호출.
환경변수:
    VLLM_LLM_URL: vLLM 엔드포인트 (default: http://localhost:8001/v1)
    VLLM_EMBED_URL: 임베딩 엔드포인트 (default: http://localhost:8002/v1)
"""

import json
import logging
import re
from typing import Any

import httpx

from prime_jennie.infra.llm.base import BaseLLMProvider, LLMResponse
from prime_jennie.infra.llm.factory import register_provider

logger = logging.getLogger(__name__)

# Ollama 모델명 → vLLM 모델명 매핑
VLLM_MODEL_MAP: dict[str, str] = {
    "exaone3.5:7.8b": "LGAI-EXAONE/EXAONE-4.0-32B-AWQ",
    "exaone": "LGAI-EXAONE/EXAONE-4.0-32B-AWQ",
}


def _extract_json(text: str) -> dict[str, Any]:
    """텍스트에서 JSON 추출 (markdown 블록, 중괄호 포함)."""
    # 1. ```json ... ``` 블록
    m = re.search(r"```json\s*(.*?)```", text, re.DOTALL)
    if m:
        return json.loads(m.group(1).strip())

    # 2. ``` ... ``` 블록
    m = re.search(r"```\s*(.*?)```", text, re.DOTALL)
    if m:
        candidate = m.group(1).strip()
        if candidate.startswith("{"):
            return json.loads(candidate)

    # 3. 첫 번째 { ... } 쌍
    start = text.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start : i + 1])

    return json.loads(text)


class OllamaLLMProvider(BaseLLMProvider):
    """vLLM 직접 호출 Provider (OpenAI-compatible API)."""

    def __init__(self) -> None:
        config = self._load_config()
        self._llm_url = config["llm_url"]
        self._embed_url = config["embed_url"]
        self._max_model_len = config["max_model_len"]
        self._default_model = config["default_model"]
        self._timeout = 300.0  # 5분

    @staticmethod
    def _load_config() -> dict:
        from prime_jennie.domain.config import get_config

        cfg = get_config().llm
        return {
            "llm_url": cfg.vllm_llm_url,
            "embed_url": cfg.vllm_embed_url,
            "max_model_len": cfg.vllm_max_model_len,
            "default_model": "LGAI-EXAONE/EXAONE-4.0-32B-AWQ",
        }

    def _resolve_model(self, model_name: str | None = None) -> str:
        """Ollama 모델명 → vLLM 모델명 해석."""
        name = model_name or self._default_model
        return VLLM_MODEL_MAP.get(name, name)

    def _clamp_max_tokens(self, prompt: str, requested: int) -> int:
        """vLLM max_model_len 초과 방지."""
        estimated_input = max(len(prompt) // 2, 100)  # 한국어 ~2 char/token
        available = self._max_model_len - estimated_input - 64
        safe_max = max(available, 256)
        return min(requested, safe_max)

    @property
    def provider_name(self) -> str:
        return "ollama"

    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        service: str | None = None,
    ) -> LLMResponse:
        model = self._resolve_model()
        clamped = self._clamp_max_tokens(prompt, max_tokens)

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": clamped,
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(f"{self._llm_url}/chat/completions", json=payload)
            resp.raise_for_status()
            data = resp.json()

        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})

        response = LLMResponse(
            content=content,
            model=model,
            tokens_in=usage.get("prompt_tokens", 0),
            tokens_out=usage.get("completion_tokens", 0),
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
        sys_msg = system or "You are a helpful assistant. Always respond with valid JSON only."
        response = await self.generate(
            prompt,
            system=sys_msg,
            temperature=temperature,
            max_tokens=max_tokens,
            service=service,
        )

        # <think> 태그 제거 (DeepSeek 호환)
        content = re.sub(r"<think>.*?</think>", "", response.content, flags=re.DOTALL).strip()

        try:
            return _extract_json(content)
        except (json.JSONDecodeError, ValueError) as err:
            logger.warning("JSON parse failed (model=%s): %s", response.model, content[:200])
            raise ValueError(f"Failed to parse JSON from LLM response: {content[:200]}") from err

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        payload = {"model": "nlpai-lab/KURE-v1", "input": texts}

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(f"{self._embed_url}/embeddings", json=payload)
            resp.raise_for_status()
            data = resp.json()

        return [item["embedding"] for item in data["data"]]


# 팩토리 자동 등록
register_provider("ollama", OllamaLLMProvider)
