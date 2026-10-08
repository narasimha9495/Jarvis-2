"""Router for managing multiple LLM providers."""

import logging
from functools import lru_cache
from typing import Any, AsyncIterator, Dict, List, Optional

from app.providers.base import BaseLLMProvider, build_messages

logger = logging.getLogger(__name__)


class LLMRouter:
    """Manage the configured LLM providers and route requests to them.

    Use `get_llm_router()` in the app — it builds ONE router from the real
    settings. `LLMRouter()` with no settings is an empty router (used in tests).
    """

    def __init__(self, settings: Any = None):
        self._providers: Dict[str, BaseLLMProvider] = {}
        self.default_provider_name = "ollama"

        if settings is None:
            return

        self.default_provider_name = getattr(settings, "default_llm_provider", "ollama")

        gemini_api_key = getattr(settings, "gemini_api_key", None)
        if gemini_api_key:
            try:
                from app.providers.gemini_provider import GeminiProvider
                model = getattr(settings, "gemini_model", "gemini-3.5-flash-lite")
                self._providers["gemini"] = GeminiProvider(api_key=gemini_api_key, model_name=model)
            except Exception as e:
                logger.warning(f"Failed to initialize Gemini provider: {e}")

        openai_api_key = getattr(settings, "openai_api_key", None)
        if openai_api_key:
            try:
                from app.providers.openai_provider import OpenAIProvider
                model = getattr(settings, "openai_model", "gpt-4o-mini")
                self._providers["openai"] = OpenAIProvider(api_key=openai_api_key, model_name=model)
            except Exception as e:
                logger.warning(f"Failed to initialize OpenAI provider: {e}")

        # Ollama needs no key, so it is always registered
        try:
            from app.providers.ollama_provider import OllamaProvider
            base_url = getattr(settings, "ollama_base_url", "http://localhost:11434")
            model = getattr(settings, "ollama_model", "llama3.2")
            self._providers["ollama"] = OllamaProvider(base_url=base_url, model_name=model)
        except Exception as e:
            logger.warning(f"Failed to initialize Ollama provider: {e}")

        if self._providers and self.default_provider_name not in self._providers:
            fallback = next(iter(self._providers))
            logger.warning(
                f"Default provider '{self.default_provider_name}' is not configured "
                f"(missing API key?). Falling back to '{fallback}'."
            )
            self.default_provider_name = fallback

    def register(self, name: str, provider: BaseLLMProvider) -> None:
        """Add or replace a provider (useful for tests and plugins)."""
        self._providers[name] = provider

    def get_provider(self, name: Optional[str] = None) -> BaseLLMProvider:
        """Get a provider by name, or the default provider.

        Raises:
            ValueError: If the provider is not configured.
        """
        target_name = name or self.default_provider_name
        if target_name not in self._providers:
            raise ValueError(
                f"Provider '{target_name}' is not configured. "
                f"Available: {list(self._providers.keys())}"
            )
        return self._providers[target_name]

    def set_default(self, name: str) -> None:
        """Change the default provider (must already be configured)."""
        self.get_provider(name)  # raises if unknown
        self.default_provider_name = name

    def get_available_providers(self) -> List[str]:
        """Return the configured provider names."""
        return list(self._providers.keys())

    async def generate(
        self,
        prompt: str = "",
        system_prompt: str = "",
        provider: Optional[str] = None,
        messages: Optional[list] = None,
    ) -> str:
        """Generate a reply.

        Pass `messages` for a multi-turn conversation (the full history is sent
        to the model, roles intact), or `prompt` + `system_prompt` for one turn.
        """
        target = self.get_provider(provider)
        conversation = messages if messages else build_messages(prompt, system_prompt)
        try:
            return await target.chat(conversation)
        except Exception as e:
            logger.error(f"Error generating with provider: {e}")
            raise

    async def generate_stream(
        self, prompt: str, system_prompt: str = "", provider: Optional[str] = None
    ) -> AsyncIterator[str]:
        """Stream a single-turn reply."""
        target = self.get_provider(provider)
        async for chunk in target.generate_stream(prompt, system_prompt):
            yield chunk

    async def list_available(self) -> List[str]:
        """List providers that pass their health check."""
        available = []
        for name, provider_instance in self._providers.items():
            try:
                if await provider_instance.health_check():
                    available.append(name)
            except Exception as e:
                logger.warning(f"Health check failed for '{name}': {e}")
        return available


@lru_cache
def get_llm_router() -> LLMRouter:
    """The app-wide router, built once from the real settings."""
    from app.config import get_settings
    return LLMRouter(get_settings())
