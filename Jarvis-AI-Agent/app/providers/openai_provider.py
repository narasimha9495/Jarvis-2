"""OpenAI LLM provider implementation."""

import logging
from typing import AsyncIterator

from app.providers.base import BaseLLMProvider, Message

logger = logging.getLogger(__name__)


class OpenAIProvider(BaseLLMProvider):
    """Provider for OpenAI chat models."""

    def __init__(self, api_key: str, model_name: str = "gpt-4o-mini"):
        self.api_key = api_key
        self.model_name = model_name
        self._client = None

    def _get_client(self):
        """Lazy-initialize the AsyncOpenAI client."""
        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError as e:
                raise RuntimeError("openai is not installed. Run: pip install openai") from e
            self._client = AsyncOpenAI(api_key=self.api_key)
        return self._client

    async def chat(self, messages: list[Message]) -> str:
        client = self._get_client()
        try:
            response = await client.chat.completions.create(
                model=self.model_name, messages=messages
            )
        except Exception as e:
            raise RuntimeError(f"OpenAI request failed: {e}") from e
        return response.choices[0].message.content or ""

    async def chat_stream(self, messages: list[Message]) -> AsyncIterator[str]:
        client = self._get_client()
        try:
            stream = await client.chat.completions.create(
                model=self.model_name, messages=messages, stream=True
            )
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as e:
            raise RuntimeError(f"OpenAI streaming failed: {e}") from e

    async def health_check(self) -> bool:
        """True if the SDK is installed and a key is set (no network call)."""
        try:
            self._get_client()
            return bool(self.api_key)
        except Exception:
            return False
