"""The worker keeps running through a database outage.

On 2026-09-24 Postgres went down for about an hour and the worker exited with
it, so ingestion stayed stopped after the database came back. The scheduler
now waits and retries instead. These tests fake the database calls; nothing
connects to Postgres.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.exc import OperationalError

from smt.worker import scheduler as scheduler_module
from smt.worker.scheduler import Scheduler


def _outage() -> OperationalError:
    return OperationalError("SELECT 1", {}, Exception("server closed the connection unexpectedly"))


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scheduler_module, "DB_RETRY_FIRST", 0.01)
    monkeypatch.setattr(scheduler_module, "DB_RETRY_MAX", 0.02)


async def test_registration_is_retried_until_the_database_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    sched = Scheduler([], tick_seconds=0.01)
    calls = {"register": 0, "tick": 0}

    async def register() -> None:
        calls["register"] += 1
        if calls["register"] < 3:
            raise _outage()

    async def tick() -> None:
        calls["tick"] += 1
        sched.stop()

    monkeypatch.setattr(sched, "register", register)
    monkeypatch.setattr(sched, "_tick", tick)
    await asyncio.wait_for(sched.run_forever(), timeout=5)
    assert calls == {"register": 3, "tick": 1}


async def test_an_outage_mid_run_does_not_stop_the_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    sched = Scheduler([], tick_seconds=0.01)
    ticks = 0

    async def register() -> None:
        return None

    async def tick() -> None:
        nonlocal ticks
        ticks += 1
        if ticks in (2, 3):
            raise _outage()
        if ticks == 5:
            sched.stop()

    monkeypatch.setattr(sched, "register", register)
    monkeypatch.setattr(sched, "_tick", tick)
    await asyncio.wait_for(sched.run_forever(), timeout=5)
    assert ticks == 5


async def test_other_errors_still_surface(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only database/network outages are waited out; a bug must not be swallowed."""
    sched = Scheduler([], tick_seconds=0.01)

    async def register() -> None:
        return None

    async def tick() -> None:
        raise ValueError("a bug")

    monkeypatch.setattr(sched, "register", register)
    monkeypatch.setattr(sched, "_tick", tick)
    with pytest.raises(ValueError, match="a bug"):
        await asyncio.wait_for(sched.run_forever(), timeout=5)
