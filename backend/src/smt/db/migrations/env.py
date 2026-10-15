"""Alembic environment: runs migrations with the sync engine against DATABASE_URL.

Pass `-x url=...` to target another database (the test suite migrates the
test database this way).
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool

from smt.config import get_settings
from smt.db import models  # noqa: F401  (registers every table on Base.metadata)
from smt.db.base import Base

target_metadata = Base.metadata


def _url() -> str:
    """Target database: `-x url=...`, then a programmatic override, then settings."""
    x_args = context.get_x_argument(as_dictionary=True)
    return (
        x_args.get("url")
        or context.config.attributes.get("x_url")
        or get_settings().database_url
    )


#: Objects created by hand-written migrations that the models don't describe: a materialized
#: view and indexes on expressions. Autogenerate must not propose dropping them.
_UNMODELED = {"entity_trade_stats", "ix_feed_events_live", "ix_entity_trade_stats_entity",
              "ix_entity_trade_stats_recent"}


def _include_object(obj: object, name: str | None, type_: str, reflected: bool, compare_to: object) -> bool:
    return name not in _UNMODELED


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True,
                      compare_type=True, include_object=_include_object)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True,
                          include_object=_include_object)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
