"""Health checks: a dead worker or a stalled EDGAR poll must be visible (smt.api.routers.health)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from smt.api.routers.health import in_filing_hours, worker_problems
from smt.db.session import session_scope

THURSDAY_NOON_ET = datetime(2026, 9, 24, 16, 0, tzinfo=UTC)   # 12:00 ET, filing hours
SUNDAY_NOON_ET = datetime(2026, 9, 27, 16, 0, tzinfo=UTC)


async def _set(job: str, last_ok: datetime | None) -> None:
    async with session_scope() as session:
        await session.execute(text(
            "INSERT INTO scheduler_jobs (name, last_ok_at) VALUES (:n, :t) "
            "ON CONFLICT (name) DO UPDATE SET last_ok_at = excluded.last_ok_at"), {"n": job, "t": last_ok})


def test_filing_hours() -> None:
    assert in_filing_hours(THURSDAY_NOON_ET)
    assert not in_filing_hours(SUNDAY_NOON_ET)
    assert not in_filing_hours(datetime(2026, 9, 25, 3, 0, tzinfo=UTC))  # 23:00 ET


@pytest.mark.db
@pytest.mark.usefixtures("db")
async def test_healthy_worker() -> None:
    await _set("worker.heartbeat", THURSDAY_NOON_ET - timedelta(seconds=40))
    await _set("edgar.poll_ownership", THURSDAY_NOON_ET - timedelta(seconds=70))
    async with session_scope() as session:
        assert await worker_problems(session, THURSDAY_NOON_ET) == []


@pytest.mark.db
@pytest.mark.usefixtures("db")
async def test_dead_worker_and_stalled_polling_are_reported() -> None:
    await _set("worker.heartbeat", THURSDAY_NOON_ET - timedelta(minutes=12))
    await _set("edgar.poll_ownership", THURSDAY_NOON_ET - timedelta(minutes=40))
    async with session_scope() as session:
        problems = await worker_problems(session, THURSDAY_NOON_ET)
    assert len(problems) == 2


@pytest.mark.db
@pytest.mark.usefixtures("db")
async def test_quiet_polling_outside_filing_hours_is_fine() -> None:
    await _set("worker.heartbeat", SUNDAY_NOON_ET - timedelta(seconds=30))
    await _set("edgar.poll_ownership", SUNDAY_NOON_ET - timedelta(hours=20))
    async with session_scope() as session:
        assert await worker_problems(session, SUNDAY_NOON_ET) == []
