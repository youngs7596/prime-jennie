"""LLM Factory — Tier 기반 Provider 라우팅.

Usage:
    from prime_jennie.infra.llm import LLMFactory

    provider = LLMFactory.get_provider("fast")      # → vLLM (EXAONE)
    provider = LLMFactory.get_provider("reasoning")  # → CloudFailover (DeepSeek)
    provider = LLMFactory.get_provider("thinking")   # → CloudFailover (DeepSeek)
"""

import logging
from functools import lru_cache

from prime_jennie.domain.config import get_config

from .base import BaseLLMProvider

logger = logging.getLogger(__name__)

# Provider 타입 → 클래스 매핑 (lazy import로 순환 참조 방지)
_PROVIDER_REGISTRY: dict[str, type[BaseLLMProvider]] = {}


def register_provider(name: str, cls: type[BaseLLMProvider]) -> None:
    """런타임에 provider 등록.

    각 provider 모듈에서 import 시 자동 등록:
        register_provider("ollama", OllamaLLMProvider)
    """
    _PROVIDER_REGISTRY[name] = cls
    logger.debug("Registered LLM provider: %s → %s", name, cls.__name__)


class LLMFactory:
    """Tier → Provider 라우팅 팩토리."""

    @staticmethod
    @lru_cache
    def get_provider(tier: str) -> BaseLLMProvider:
        """tier (fast|reasoning|thinking) → provider 인스턴스.

        Config에서 TIER_{tier}_PROVIDER를 읽어 해당 provider 생성.
        """
        config = get_config().llm
        tier_lower = tier.lower()

        provider_type = {
            "fast": config.tier_fast_provider,
            "reasoning": config.tier_reasoning_provider,
            "thinking": config.tier_thinking_provider,
        }.get(tier_lower)

        if not provider_type:
            raise ValueError(f"Unknown LLM tier: {tier}")

        # Lazy import: provider 모듈이 아직 로드 안 됐으면 시도
        if provider_type not in _PROVIDER_REGISTRY:
            _try_import_provider(provider_type)

        provider_cls = _PROVIDER_REGISTRY.get(provider_type)
        if not provider_cls:
            raise ValueError(
                f"LLM provider '{provider_type}' not registered. Available: {list(_PROVIDER_REGISTRY.keys())}"
            )

        logger.info("LLM tier=%s → provider=%s", tier, provider_type)
        return provider_cls()


def _try_import_provider(provider_type: str) -> None:
    """Provider 모듈 lazy import."""
    import_map = {
        "ollama": "prime_jennie.infra.llm.providers.ollama",
        "openai": "prime_jennie.infra.llm.providers.openai_provider",
        "claude": "prime_jennie.infra.llm.providers.claude",
        "gemini": "prime_jennie.infra.llm.providers.gemini",
        "deepseek_cloud": "prime_jennie.infra.llm.providers.deepseek_cloud",
    }
    module_path = import_map.get(provider_type)
    if module_path:
        try:
            import importlib

            importlib.import_module(module_path)
        except ImportError as e:
            logger.warning("Failed to import %s: %s", module_path, e)
