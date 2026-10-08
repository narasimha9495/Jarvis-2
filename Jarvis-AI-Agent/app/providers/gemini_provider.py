"""Gemini LLM provider using the current Google Gen AI SDK (`google-genai`).

The older `google-generativeai` package is deprecated, so this provider
uses `from google import genai` instead.
"""

import logging
from typing import AsyncIterator

from app.providers.base import BaseLLMProvider, Message, split_system

logger = logging.getLogger(__name__)


class GeminiProvider(BaseLLMProvider):
    """Provider for Google's Gemini models."""

    def __init__(self, api_key: str, model_name: str = "gemini-3.5-flash-lite"):
        self.api_key = api_key
        self.model_name = model_name
        self._client = None

    def _get_client(self):
        """Lazy-create the Gen AI client."""
        if self._client is None:
            try:
                from google import genai
            except ImportError as e:
                raise RuntimeError(
                    "google-genai is not installed. Run: pip install google-genai"
                ) from e
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def _build_request(self, messages: list[Message]):
        """Convert chat messages into Gemini `contents` + config."""
        from google.genai import types

        system, rest = split_system(messages)
        contents = [
            types.Content(
                # Gemini calls the assistant role "model"
                role="model" if m["role"] == "assistant" else "user",
                parts=[types.Part(text=m["content"])],
            )
            for m in rest
        ]
        config = types.GenerateContentConfig(system_instruction=system or None)
        return contents, config

    async def chat(self, messages: list[Message]) -> str:
        client = self._get_client()
        contents, config = self._build_request(messages)
        try:
            response = await client.aio.models.generate_content(
                model=self.model_name, contents=contents, config=config
            )
        except Exception as e:
            raise RuntimeError(f"Gemini request failed: {e}") from e
        # .text is None when the reply was blocked or empty
        return response.text or ""

    async def chat_stream(self, messages: list[Message]) -> AsyncIterator[str]:
        client = self._get_client()
        contents, config = self._build_request(messages)
        try:
            stream = await client.aio.models.generate_content_stream(
                model=self.model_name, contents=contents, config=config
            )
            async for chunk in stream:
                if chunk.text:
                    yield chunk.text
        except Exception as e:
            raise RuntimeError(f"Gemini streaming failed: {e}") from e

    async def health_check(self) -> bool:
        """True if the SDK is installed and a key is set (no network call)."""
        try:
            self._get_client()
            return bool(self.api_key)
        except Exception:
            return False
