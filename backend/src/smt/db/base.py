"""Declarative base and shared column helpers.

Constraint names follow a fixed convention so Alembic migrations are stable
and readable (e.g. `uq_filings_source_source_key`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from sqlalchemy import BigInteger, DateTime, MetaData, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base class for every table."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {  # noqa: RUF012 - SQLAlchemy reads this class attribute
        dict[str, Any]: JSONB,
        datetime: DateTime(timezone=True),
    }


#: Surrogate primary key: 64-bit identity column.
BigIntPK = Annotated[int, mapped_column(BigInteger, primary_key=True, autoincrement=True)]
#: Timestamp defaulting to the database clock (always stored as UTC timestamptz).
CreatedAt = Annotated[datetime, mapped_column(DateTime(timezone=True), server_default=func.now())]
