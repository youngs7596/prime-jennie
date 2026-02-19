"""LLM Provider 단위 테스트 — mock 기반 (실제 API 호출 없음)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prime_jennie.infra.llm.base import BaseLLMProvider, LLMResponse
from prime_jennie.infra.llm.factory import LLMFactory, _PROVIDER_REGISTRY, register_provider


@pytest.fixture(autouse=True)
def _clear_caches():
    from prime_jennie.domain.config import get_config
    get_config.cache_clear()
    LLMFactory.get_provider.cache_clear()
    yield
    get_config.cache_clear()
    LLMFactory.get_provider.cache_clear()


# ─── Base & Factory ──────────────────────────────────────────


class TestBaseLLMProvider:
    def test_abstract_methods(self):
        """추상 클래스는 직접 인스턴스화 불가."""
        with pytest.raises(TypeError):
            BaseLLMProvider()

    def test_llm_response_model(self):
        resp = LLMResponse(
            content="hello", model="test", tokens_in=10, tokens_out=5, provider="test"
        )
        assert resp.content == "hello"
        assert resp.tokens_in == 10


class TestLLMFactory:
    def test_unknown_tier_raises(self):
        with pytest.raises(ValueError, match="Unknown LLM tier"):
            LLMFactory.get_provider("nonexistent")

    def test_register_and_get(self):
        """커스텀 provider 등록 후 조회."""

        class MockProvider(BaseLLMProvider):
            async def generate(self, prompt, **kwargs):
                return LLMResponse(content="mock", model="m", provider="mock")

            async def generate_json(self, prompt, schema, **kwargs):
                return {"result": "mock"}

            async def generate_embeddings(self, texts):
                return [[0.1] * 768]

            @property
            def provider_name(self):
                return "mock"

        register_provider("test_mock", MockProvider)
        assert "test_mock" in _PROVIDER_REGISTRY

        # 정리
        del _PROVIDER_REGISTRY["test_mock"]


# ─── Ollama Provider ──────────────────────────────────────────


class TestOllamaProvider:
    @patch("prime_jennie.infra.llm.providers.ollama.OllamaLLMProvider._load_config")
    def test_resolve_model_mapping(self, mock_config):
        mock_config.return_value = {
            "llm_url": "http://localhost:8001/v1",
            "embed_url": "http://localhost:8002/v1",
            "max_model_len": 4096,
            "default_model": "LGAI-EXAONE/EXAONE-4.0-32B-AWQ",
        }
        from prime_jennie.infra.llm.providers.ollama import OllamaLLMProvider

        p = OllamaLLMProvider()
        assert p._resolve_model("exaone3.5:7.8b") == "LGAI-EXAONE/EXAONE-4.0-32B-AWQ"
        assert p._resolve_model("unknown-model") == "unknown-model"

    @patch("prime_jennie.infra.llm.providers.ollama.OllamaLLMProvider._load_config")
    def test_clamp_max_tokens(self, mock_config):
        mock_config.return_value = {
            "llm_url": "http://localhost:8001/v1",
            "embed_url": "http://localhost:8002/v1",
            "max_model_len": 4096,
            "default_model": "test",
        }
        from prime_jennie.infra.llm.providers.ollama import OllamaLLMProvider

        p = OllamaLLMProvider()
        # 긴 프롬프트 → 토큰 클램핑
        long_prompt = "가" * 8000  # ~4000 tokens
        result = p._clamp_max_tokens(long_prompt, 2048)
        assert result <= 2048
        assert result >= 256

    @patch("prime_jennie.infra.llm.providers.ollama.OllamaLLMProvider._load_config")
    def test_provider_name(self, mock_config):
        mock_config.return_value = {
            "llm_url": "http://localhost:8001/v1",
            "embed_url": "http://localhost:8002/v1",
            "max_model_len": 4096,
            "default_model": "test",
        }
        from prime_jennie.infra.llm.providers.ollama import OllamaLLMProvider

        p = OllamaLLMProvider()
        assert p.provider_name == "ollama"


class TestExtractJson:
    def test_plain_json(self):
        from prime_jennie.infra.llm.providers.ollama import _extract_json

        result = _extract_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_markdown_block(self):
        from prime_jennie.infra.llm.providers.ollama import _extract_json

        text = 'Here is the result:\n```json\n{"score": 75}\n```\nDone.'
        assert _extract_json(text) == {"score": 75}

    def test_code_block_without_json_tag(self):
        from prime_jennie.infra.llm.providers.ollama import _extract_json

        text = '```\n{"a": 1}\n```'
        assert _extract_json(text) == {"a": 1}

    def test_nested_braces(self):
        from prime_jennie.infra.llm.providers.ollama import _extract_json

        text = 'Result: {"outer": {"inner": 42}} end'
        result = _extract_json(text)
        assert result["outer"]["inner"] == 42

    def test_invalid_json_raises(self):
        from prime_jennie.infra.llm.providers.ollama import _extract_json

        with pytest.raises((json.JSONDecodeError, ValueError)):
            _extract_json("not json at all")


# ─── OpenAI Provider ──────────────────────────────────────────


class TestOpenAIProvider:
    def test_reasoning_model_detection(self):
        from prime_jennie.infra.llm.providers.openai_provider import OpenAILLMProvider

        with patch.dict("os.environ", {"OPENAI_API_KEY": "test"}):
            p = OpenAILLMProvider(api_key="test")
            assert p._is_reasoning_model("o3-mini") is True
            assert p._is_reasoning_model("gpt-4o-mini") is False
            assert p._is_reasoning_model("gpt-5.2") is True

    def test_provider_name(self):
        from prime_jennie.infra.llm.providers.openai_provider import OpenAILLMProvider

        p = OpenAILLMProvider(api_key="test")
        assert p.provider_name == "openai"


# ─── Claude Provider ──────────────────────────────────────────


class TestClaudeProvider:
    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    def test_provider_name(self):
        from prime_jennie.infra.llm.providers.claude import ClaudeLLMProvider

        p = ClaudeLLMProvider()
        assert p.provider_name == "claude"

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    def test_embeddings_not_supported(self):
        from prime_jennie.infra.llm.providers.claude import ClaudeLLMProvider

        p = ClaudeLLMProvider()
        with pytest.raises(NotImplementedError):
            import asyncio
            asyncio.get_event_loop().run_until_complete(
                p.generate_embeddings(["test"])
            )


# ─── CloudFailover Provider ──────────────────────────────────


class TestCloudFailoverProvider:
    @patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-ds-key"})
    def test_initializes_with_deepseek(self):
        from prime_jennie.infra.llm.providers.deepseek_cloud import (
            CloudFailoverProvider,
        )

        p = CloudFailoverProvider()
        assert len(p._providers) >= 1
        assert p._providers[0][0] == "deepseek-api"

    @patch("prime_jennie.infra.llm.providers.deepseek_cloud.CloudFailoverProvider._init_chain")
    def test_no_providers_raises(self, mock_init):
        """모든 provider 초기화 실패 시 RuntimeError."""
        from prime_jennie.infra.llm.providers.deepseek_cloud import (
            CloudFailoverProvider,
        )

        mock_init.return_value = None  # _providers stays empty
        with pytest.raises(RuntimeError, match="No providers available"):
            CloudFailoverProvider()

    @patch.dict("os.environ", {"DEEPSEEK_API_KEY": "k1", "OPENROUTER_API_KEY": "k2"})
    def test_chain_order(self):
        from prime_jennie.infra.llm.providers.deepseek_cloud import (
            CloudFailoverProvider,
        )

        p = CloudFailoverProvider()
        names = [n for n, _ in p._providers]
        assert names[0] == "deepseek-api"
        assert names[1] == "openrouter"

    def test_provider_name(self):
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test"}):
            from prime_jennie.infra.llm.providers.deepseek_cloud import (
                CloudFailoverProvider,
            )

            p = CloudFailoverProvider()
            assert p.provider_name == "deepseek_cloud"

    @pytest.mark.asyncio
    async def test_failover_calls_next_on_error(self):
        from prime_jennie.infra.llm.providers.deepseek_cloud import (
            CloudFailoverProvider,
        )

        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test"}):
            p = CloudFailoverProvider()

        # Mock providers
        mock1 = AsyncMock()
        mock1.generate_json = AsyncMock(side_effect=Exception("API down"))
        mock2 = AsyncMock()
        mock2.generate_json = AsyncMock(return_value={"result": "ok"})

        p._providers = [("provider-1", mock1), ("provider-2", mock2)]

        result = await p._failover_call(
            "generate_json", prompt="test", schema={}, temperature=0.3
        )
        assert result == {"result": "ok"}
        mock1.generate_json.assert_called_once()
        mock2.generate_json.assert_called_once()

    @pytest.mark.asyncio
    async def test_all_fail_raises(self):
        from prime_jennie.infra.llm.providers.deepseek_cloud import (
            CloudFailoverProvider,
        )

        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test"}):
            p = CloudFailoverProvider()

        mock1 = AsyncMock()
        mock1.generate = AsyncMock(side_effect=Exception("fail1"))
        mock2 = AsyncMock()
        mock2.generate = AsyncMock(side_effect=Exception("fail2"))

        p._providers = [("p1", mock1), ("p2", mock2)]

        with pytest.raises(RuntimeError, match="All CloudFailover"):
            await p._failover_call("generate", prompt="test")
