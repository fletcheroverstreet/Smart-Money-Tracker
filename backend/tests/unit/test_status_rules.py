"""When /status calls a job "behind schedule" (smt.api.routers.status._is_stale)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from smt.api.routers.status import _is_stale

NOW = datetime(2026, 9, 24, 22, 43, tzinfo=UTC)
DAY = 26 * 3600


def test_a_recent_success_is_on_time() -> None:
    assert not _is_stale(DAY, NOW - timedelta(hours=3), NOW + timedelta(hours=20), NOW)


def test_no_success_within_the_limit_is_behind() -> None:
    assert _is_stale(DAY, NOW - timedelta(hours=27), NOW - timedelta(hours=1), NOW)


def test_a_nightly_job_waiting_for_its_first_run_is_not_behind() -> None:
    # Registered this morning, first run tonight at 11 PM ET: nothing is late yet.
    assert not _is_stale(DAY, None, NOW + timedelta(hours=4), NOW)


def test_a_job_that_never_ran_is_behind_once_overdue() -> None:
    assert not _is_stale(DAY, None, NOW - timedelta(minutes=10), NOW)
    assert _is_stale(DAY, None, NOW - timedelta(hours=2), NOW)


def test_jobs_without_a_limit_are_never_behind() -> None:
    assert not _is_stale(None, None, NOW - timedelta(days=9), NOW)
