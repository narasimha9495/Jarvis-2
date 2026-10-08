"""Application configuration using pydantic-settings.

Loads settings from the project's .env file and environment variables.
Paths are resolved from the project folder, so the app behaves the same
no matter which directory you start it from.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # LLM API keys
    gemini_api_key: str = Field(default="", description="Google Gemini API key")
    openai_api_key: str = Field(default="", description="OpenAI API key")

    # Ollama
    ollama_base_url: str = Field(default="http://localhost:11434", description="Ollama server URL")

    # Default provider: gemini, openai, or ollama
    default_llm_provider: str = Field(default="gemini")

    # Model names (change these in .env when a provider retires a model)
    gemini_model: str = Field(default="gemini-3.5-flash-lite")
    openai_model: str = Field(default="gpt-4o-mini")
    ollama_model: str = Field(default="llama3.2")

    # App — 127.0.0.1 keeps Jarvis reachable only from this computer.
    # Set APP_HOST=0.0.0.0 only if you deliberately want other devices to reach it.
    app_host: str = Field(default="127.0.0.1")
    app_port: int = Field(default=8000)
    debug: bool = Field(default=False)

    # Extra browser origins allowed to call the API / WebSocket
    # (the page Jarvis serves itself is always allowed).
    allowed_origins: list[str] = Field(default_factory=list)

    # Database (absolute path, independent of the current folder)
    database_url: str = Field(
        default_factory=lambda: f"sqlite+aiosqlite:///{(BASE_DIR / 'jarvis.db').as_posix()}"
    )

    # Behaviour
    currency_symbol: str = Field(default="₹")
    max_history_messages: int = Field(default=20, description="Chat turns kept as LLM context")
    reminder_check_seconds: int = Field(default=30, description="How often due reminders are checked")

    base_dir: Path = Field(default=BASE_DIR)

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return the cached Settings instance (reads .env once)."""
    return Settings()
