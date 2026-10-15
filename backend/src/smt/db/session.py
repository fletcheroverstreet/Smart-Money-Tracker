"""Database engines and sessions.

Two engines exist:

* an **async** engine for the worker and the API (psycopg 3, async mode);
* a **sync** engine for Alembic migrations, bulk loads and scripts.

Both are created lazily from settings, never at import time.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from smt.config import get_settings


@lru_cache(maxsize=4)
def async_engine(url: str | None = None) -> AsyncEngine:
    """Return the shared async engine for `url` (default: DATABASE_URL)."""
    return create_async_engine(
        url or get_settings().database_url,
        pool_size=10,
        max_overflow=10,
        pool_pre_ping=True,
    )


@lru_cache(maxsize=4)
def sync_engine(url: str | None = None) -> Engine:
    """Return the shared sync engine for `url` (default: DATABASE_URL)."""
    return create_engine(url or get_settings().database_url, pool_pre_ping=True)


def session_factory(url: str | None = None) -> async_sessionmaker[AsyncSession]:
    """Return a session factory bound to the async engine."""
    return async_sessionmaker(async_engine(url), expire_on_commit=False)


@asynccontextmanager
async def session_scope(url: str | None = None) -> AsyncIterator[AsyncSession]:
    """Open a session, commit on success, roll back on error.

    Loaders use this so that a filing and every row derived from it are
    committed together or not at all.
    """
    async with session_factory(url)() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


async def dispose_engines() -> None:
    """Close every pooled connection (used at shutdown and between tests)."""
    for url in list(_engine_urls()):
        await async_engine(url).dispose()
    async_engine.cache_clear()
    sync_engine.cache_clear()


def _engine_urls() -> list[str | None]:
    # lru_cache does not expose its keys; the default engine is the only one
    # used outside tests, and tests call cache_clear() themselves.
    return [None]
