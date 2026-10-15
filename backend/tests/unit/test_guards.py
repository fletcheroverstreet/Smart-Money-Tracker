"""The isolation guards must actually trip. A guard that never fires protects nothing."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from conftest import TEST_DB_URL, assert_not_real_finlake_home, assert_safe_test_database
from smt.config import get_settings


def test_refuses_a_database_not_named_test() -> None:
    with pytest.raises(RuntimeError, match="does not end in '_test'"):
        assert_safe_test_database("postgresql+psycopg://postgres@localhost:5433/smt", ci=False)


def test_refuses_a_remote_host_outside_ci() -> None:
    with pytest.raises(RuntimeError, match="non-local host"):
        assert_safe_test_database("postgresql+psycopg://u@prod.example.com:5432/smt_test", ci=False)


def test_allows_a_remote_test_host_in_ci() -> None:
    assert_safe_test_database("postgresql+psycopg://u@ci-db.internal:5432/smt_test", ci=True)


def test_refuses_the_real_finlake_home() -> None:
    with pytest.raises(RuntimeError, match=r"REAL ~/\.finlake"):
        assert_not_real_finlake_home(Path.home() / ".finlake")


def test_settings_point_at_test_resources() -> None:
    settings = get_settings()
    assert settings.database_url == TEST_DB_URL
    assert settings.database_url.endswith("_test")
    assert settings.environment == "test"
    home = Path.home().resolve()
    assert not str(settings.raw_store_dir.resolve()).startswith(str(home / ".smart-money-tracker"))


def test_finlake_home_is_redirected_before_any_import() -> None:
    assert os.environ["FINLAKE_HOME"] != str(Path.home() / ".finlake")
