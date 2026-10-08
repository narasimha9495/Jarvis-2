"""Abstract base class for LLM providers.

Every provider works on a list of chat messages:

    [{"role": "system" | "user" | "assistant", "content": "..."}]

so the model sees the real conversation (including its own previous
replies) instead of a flattened prompt.
"""

from abc import ABC, abstractmethod
from typing import AsyncIterator

Message = dict[str, str]


class BaseLLMProvider(ABC):
    """Base class that all LLM providers must implement."""

    @abstractmethod
    async def chat(self, messages: list[Message]) -> str:
        """Return the model's reply to a full conversation.

        Args:
            messages: Ordered chat messages. System messages may appear first.

        Returns:
            The model's text response.
        """
        ...

    @abstractmethod
    async def chat_stream(self, messages: list[Message]) -> AsyncIterator[str]:
        """Stream the model's reply to a full conversation, chunk by chunk."""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if the provider is available and configured."""
        ...

    # ── Convenience wrappers for single-turn use ──

    async def generate(self, prompt: str, system_prompt: str = "") -> str:
        """Single-turn helper: one optional system prompt plus one user prompt."""
        return await self.chat(build_messages(prompt, system_prompt))

    async def generate_stream(self, prompt: str, system_prompt: str = "") -> AsyncIterator[str]:
        """Single-turn streaming helper."""
        async for chunk in self.chat_stream(build_messages(prompt, system_prompt)):
            yield chunk


def build_messages(prompt: str, system_prompt: str = "") -> list[Message]:
    """Build a message list from a prompt and an optional system prompt."""
    messages: list[Message] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    return messages


def split_system(messages: list[Message]) -> tuple[str, list[Message]]:
    """Separate system messages (joined) from the rest of the conversation."""
    system = "\n\n".join(m["content"] for m in messages if m.get("role") == "system")
    rest = [m for m in messages if m.get("role") != "system"]
    return system, rest
