"""FastAPI dependency injection.

Kept for backwards compatibility — the single source of settings is
`app.config.get_settings`.
"""

from app.config import Settings, get_settings
from app.core.llm_router import get_llm_router

__all__ = ["Settings", "get_settings", "get_llm_router"]
