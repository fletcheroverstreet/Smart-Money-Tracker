"""Test harness. Isolation happens HERE, before any test module imports `smt`.

pytest imports conftest.py before it collects test modules, so the
environment set at the top of this file is in place before anything reads
settings. That ordering is the whole point. finlake's suite once deleted
15.5M real rows because a module-level import bound the data path first.

Guards (each one raises before any test runs):

1. The test database name must end in `_test`, and the host must be local
   (or CI). The real `smt` database can never be touched.
2. Every local data directory (raw store, SMT_HOME, FINLAKE_HOME) points into
   a fresh temp directory. If finlake gets imported anyway, its resolved data
   path is checked against the real `~/.finlake`.
3. No network: tests use respx mocks. Tests that really need the internet are
   marked `network` and skipped unless SMT_NETWORK_TESTS=1.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from urllib.parse import urlparse

# ---------------------------------------------------------------- environment first
_TMP = Path(tempfile.mkdtemp(prefix="smt-tests-"))
os.environ["ENVIRONMENT"] = "test"
os.environ["SMT_HOME"] = str(_TMP / "home")
os.environ["RAW_STORE_DIR"] = str(_TMP / "raw")
os.environ["FINLAKE_HOME"] = str(_TMP / "finlake")  # finlake binds this at import
os.environ.setdefault("SEC_USER_AGENT", "SmartMoneyTracker-tests test@example.com")
os.environ.setdefault(
    "TEST_DATABASE_URL", "postgresql+psycopg://postgres@localhost:5433/smt_test"
)
TEST_DB_URL = os.environ["TEST_DATABASE_URL"]
# Every code path that reads DATABASE_URL now gets the test database.
os.environ["DATABASE_URL"] = TEST_DB_URL

_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "::1", "postgres", "db"}


def assert_safe_test_database(url: str, *, ci: bool) -> None:
    """Raise unless `url` is clearly a disposable test database."""
    parsed = urlparse(url.replace("postgresql+psycopg://", "postgresql://"))
    name = parsed.path.lstrip("/")
    if not name.endswith("_test"):
        raise RuntimeError(f"Refusing to run tests: database '{name}' does not end in '_test'.")
    if parsed.hostname not in _ALLOWED_HOSTS and not ci:
        raise RuntimeError(f"Refusing to run tests against non-local host '{parsed.hostname}'.")


def assert_not_real_finlake_home(resolved: Path) -> None:
    """Raise if a finlake data path resolved to the user's real cache."""
    real = (Path.home() / ".finlake").resolve()
    if resolved.resolve() == real:
        raise RuntimeError("finlake resolved to the REAL ~/.finlake during tests. Aborting.")


assert_safe_test_database(TEST_DB_URL, ci=os.environ.get("CI") == "true")

import pytest  # noqa: E402  (imported after the environment is fixed on purpose)

from smt.config import get_settings, reset_settings_cache  # noqa: E402

reset_settings_cache()
assert get_settings().database_url == TEST_DB_URL, "settings did not pick up the test database"

if "finlake" in sys.modules or "finlake.config" in sys.modules:  # pragma: no cover
    from finlake import config as _finlake_config

    assert_not_real_finlake_home(Path(_finlake_config.DATA_DIR))


# ---------------------------------------------------------------- event loop
def pytest_asyncio_loop_factories(config: pytest.Config, item: pytest.Item) -> Mapping[str, object]:
    """psycopg async needs a SelectorEventLoop on Windows (see smt.runtime)."""
    if sys.platform == "win32":
        return {"selector": asyncio.SelectorEventLoop}
    return {"default": asyncio.new_event_loop}


# ---------------------------------------------------------------- markers
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("SMT_NETWORK_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="network test (set SMT_NETWORK_TESTS=1 to run)")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip)


# ---------------------------------------------------------------- database
@pytest.fixture(scope="session")
def migrated_db() -> str:
    """Drop and recreate the test schema, then run every Alembic migration once."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    engine = create_engine(TEST_DB_URL)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()

    backend_dir = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "src/smt/db/migrations"))
    cfg.cmd_opts = None
    cfg.attributes["x_url"] = TEST_DB_URL
    command.upgrade(cfg, "head")
    return TEST_DB_URL


@pytest.fixture
async def db(migrated_db: str) -> AsyncIterator[None]:
    """Empty every table before a test (fast TRUNCATE), keep the schema."""
    from sqlalchemy import text

    from smt.db.base import Base
    from smt.db.session import async_engine

    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    engine = async_engine(migrated_db)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield
    await engine.dispose()


@pytest.fixture
def raw_dir() -> Path:
    """The per-run raw document store (inside the temp directory)."""
    return Path(os.environ["RAW_STORE_DIR"])
