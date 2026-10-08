"""Async SQLAlchemy database setup."""

import logging
from typing import AsyncGenerator

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.config import get_settings
from app.db.models import Base

logger = logging.getLogger(__name__)

engine = create_async_engine(get_settings().database_url, echo=False)
async_session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

# Columns added after the first release. create_all() never alters existing
# tables, so older jarvis.db files get these added here.
_ADDED_COLUMNS = {
    "tasks": {"completed_at": "DATETIME"},
}


def _add_missing_columns(sync_conn) -> None:
    inspector = inspect(sync_conn)
    for table, columns in _ADDED_COLUMNS.items():
        if not inspector.has_table(table):
            continue
        existing = {c["name"] for c in inspector.get_columns(table)}
        for name, sql_type in columns.items():
            if name not in existing:
                logger.info(f"Upgrading database: adding {table}.{name}")
                sync_conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"))


async def init_db(db_engine=None) -> None:
    """Create tables and apply small schema upgrades."""
    db_engine = db_engine or engine
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: one session per request."""
    async with async_session_maker() as session:
        yield session
