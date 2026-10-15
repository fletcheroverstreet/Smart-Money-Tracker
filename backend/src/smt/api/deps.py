"""Shared API dependencies."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from smt.db.session import session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    """A read-only request session. The API never writes disclosure data."""
    async with session_factory()() as session:
        yield session


Session = Annotated[AsyncSession, Depends(get_session)]
