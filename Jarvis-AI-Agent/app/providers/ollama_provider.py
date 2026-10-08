"""Ollama LLM provider implementation (local / offline)."""

import logging
from typing import AsyncIterator

from app.providers.base import BaseLLMProvider, Message

logger = logging.getLogger(__name__)


class OllamaProvider(BaseLLMProvider):
    """Provider for models served by a local Ollama server."""

    def __init__(self, base_url: str = "http://localhost:11434", model_name: str = "llama3.2"):
        self.base_url = base_url
        self.model_name = model_name
        self._client = None

    def _get_client(self):
        """Lazy-initialize the Ollama async client."""
        if self._client is None:
            try:
                import ollama
            except ImportError as e:
                raise RuntimeError("ollama is not installed. Run: pip install ollama") from e
            self._client = ollama.AsyncClient(host=self.base_url)
        return self._client

    async def chat(self, messages: list[Message]) -> str:
        client = self._get_client()
        try:
            response = await client.chat(model=self.model_name, messages=messages)
        except Exception as e:
            raise RuntimeError(
                f"Ollama request failed ({e}). Is Ollama running at {self.base_url} "
                f"and is '{self.model_name}' pulled?"
            ) from e
        return response.message.content or ""

    async def chat_stream(self, messages: list[Message]) -> AsyncIterator[str]:
        client = self._get_client()
        try:
            stream = await client.chat(model=self.model_name, messages=messages, stream=True)
            async for chunk in stream:
                if chunk.message.content:
                    yield chunk.message.content
        except Exception as e:
            raise RuntimeError(f"Ollama streaming failed: {e}") from e

    async def health_check(self) -> bool:
        """Check that the Ollama server is reachable."""
        try:
            await self._get_client().list()
            return True
        except Exception:
            return False
