"""A restarted worker takes back its predecessor's leases (smt.worker.scheduler.Scheduler.register).

Observed on 2026-09-24: after a worker restart, live polling stood still for about
11 minutes, waiting for the dead process's 10-minute lease to expire.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import text

from smt.db.session import session_scope
from smt.worker.scheduler import Job, JobContext, Scheduler, run_job_once
from smt.worker.schedules import Every

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]


async def _noop(ctx: JobContext) -> None:
    return None


async def test_leases_of_a_dead_process_on_this_machine_are_released() -> None:
    sched = Scheduler([Job("edgar.poll_ownership", "edgar", _noop, Every(60), lease=timedelta(minutes=10))])
    host = sched.me.rsplit(":", 1)[0]
    async with session_scope() as session:
        await session.execute(text(
            "INSERT INTO scheduler_jobs (name, lease_until, leased_by) VALUES "
            "('edgar.poll_ownership', now() + interval '9 minutes', :old), "
            "('other.job', now() + interval '9 minutes', 'another-host:123')"),
            {"old": f"{host}:{999999}"})
    await sched.register()
    async with session_scope() as session:
        leases = dict((await session.execute(text("SELECT name, leased_by FROM scheduler_jobs"))).tuples().all())
    assert leases["edgar.poll_ownership"] is None          # ours, from a process that is gone: freed
    assert leases["other.job"] == "another-host:123"         # another machine's lease is left alone


async def test_a_run_that_outlives_its_lease_is_cancelled() -> None:
    """A run hung forever (e.g. a request made just before the machine slept) must not block the job."""
    import asyncio

    hang = asyncio.Event()

    async def hangs(ctx: JobContext) -> None:
        await hang.wait()  # never set

    sched = Scheduler([Job("edgar.process_queue", "edgar", hangs, Every(60), lease=timedelta(seconds=0.2),
                           run_at_start=True)], tick_seconds=0.05)
    await sched.register()
    await sched._tick()
    [task] = sched._running["edgar.process_queue"]
    # Tick until the run is past its lease and cancelled (waiting on events, not fixed sleeps,
    # so a busy machine can't make this flaky); give up after 5 seconds.
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5
    while not task.done() and loop.time() < deadline:
        await asyncio.sleep(0.1)
        await sched._tick()
    await asyncio.wait({task}, timeout=5)
    assert task.done()
    await asyncio.sleep(0)  # the done-callback that forgets the run runs on the next loop turn
    assert "edgar.process_queue" not in sched._running
    async with session_scope() as session:
        status, error = (await session.execute(text(
            "SELECT status, error FROM ingestion_runs WHERE job = 'edgar.process_queue'"))).one()
    assert status == "failed"
    assert "outlived its lease" in error


async def _job_row(name: str) -> Any:
    async with session_scope() as session:
        return (await session.execute(text(
            "SELECT next_run_at, lease_until, leased_by, updated_at, cursor, last_run_id, last_ok_at "
            "FROM scheduler_jobs WHERE name = :name"), {"name": name})).one()


async def _runs(name: str) -> list[Any]:
    async with session_scope() as session:
        return list((await session.execute(text(
            "SELECT id, status, error, stats FROM ingestion_runs WHERE job = :name ORDER BY id"),
            {"name": name})).all())


def _slow_finish(sched: Scheduler, monkeypatch: pytest.MonkeyPatch) -> asyncio.Event:
    """Make the run's bookkeeping slow, so a second cancel can land in the middle of it."""
    original = sched._finish
    entered = asyncio.Event()

    async def slow(*args: Any, **kwargs: Any) -> None:
        entered.set()
        await asyncio.sleep(0.3)
        await original(*args, **kwargs)

    monkeypatch.setattr(sched, "_finish", slow)
    return entered


async def test_a_second_cancel_does_not_interrupt_the_bookkeeping(monkeypatch: pytest.MonkeyPatch) -> None:
    """Critic round 6: the overrun check cancelled a run again while it was still recording its end.
    The CancelledError escaped the bookkeeping, so the run stayed 'running', the job kept its
    lease and next_run_at, and the job started again at once (one full test run failed)."""
    hang = asyncio.Event()

    async def hangs(ctx: JobContext) -> None:
        await hang.wait()  # never set

    sched = Scheduler([Job("edgar.process_queue", "edgar", hangs, Every(60), lease=timedelta(minutes=10),
                           run_at_start=True)])
    await sched.register()
    before = (await _job_row("edgar.process_queue")).next_run_at
    await sched._tick()
    [task] = sched._running["edgar.process_queue"]
    await asyncio.sleep(0.1)  # the run is under way
    entered = _slow_finish(sched, monkeypatch)
    task.cancel()
    await asyncio.wait_for(entered.wait(), timeout=5)
    task.cancel()  # the second cancel lands in the bookkeeping
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    [run] = await _runs("edgar.process_queue")
    assert run.status == "failed"
    assert "cancelled" in run.error
    row = await _job_row("edgar.process_queue")
    assert row.next_run_at > before + timedelta(seconds=30)  # advanced by the schedule (60 s)
    assert row.leased_by is None
    assert row.lease_until is None


async def test_the_overrun_check_does_not_cancel_a_run_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    hang = asyncio.Event()

    async def hangs(ctx: JobContext) -> None:
        await hang.wait()  # never set

    sched = Scheduler([Job("edgar.process_queue", "edgar", hangs, Every(60), lease=timedelta(seconds=0.2),
                           run_at_start=True)])
    await sched.register()
    await sched._tick()
    [task] = sched._running["edgar.process_queue"]
    entered = _slow_finish(sched, monkeypatch)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5
    while not entered.is_set() and loop.time() < deadline:
        await asyncio.sleep(0.05)
        sched._cancel_overruns()
    assert entered.is_set()
    for _ in range(3):  # ticks while the run records its end: no further cancel
        sched._cancel_overruns()
        assert task.cancelling() == 1
        await asyncio.sleep(0.05)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    [run] = await _runs("edgar.process_queue")
    assert run.status == "failed"
    assert (await _job_row("edgar.process_queue")).leased_by is None


async def test_an_overlapping_job_keeps_a_fixed_cadence_with_at_most_n_runs_in_flight() -> None:
    """The live Form 4 read (critic round 6, P1): a read starts on time even while earlier reads
    still wait for a slow EDGAR page, up to max_in_flight. The lease is held by this worker for all
    of them and released by the last one; the cursor is never written."""
    name = "edgar.poll_ownership"
    gates: dict[int, asyncio.Event] = {}

    async def read(ctx: JobContext) -> None:
        gates[ctx.run_id] = asyncio.Event()
        await gates[ctx.run_id].wait()

    sched = Scheduler([Job(name, "edgar", read, Every(0.1), lease=timedelta(minutes=10), run_at_start=True,
                           max_in_flight=3)])
    await sched.register()
    async with session_scope() as session:
        await session.execute(text(
            "UPDATE scheduler_jobs SET cursor = CAST(:c AS jsonb) WHERE name = :name"),
            {"c": '{"watermarks": {"4": "2026-09-28T16:00:00-04:00"}}', "name": name})
    await sched._tick()
    first = await _job_row(name)
    assert first.leased_by == sched.me
    for _ in range(8):  # about a second of ticks: the cap stops it at 3
        await asyncio.sleep(0.12)
        await sched._tick()
    assert len(sched._running[name]) == 3
    held = await _job_row(name)
    assert held.updated_at == first.updated_at  # extended, not re-taken (the watchdog relies on it)
    assert held.lease_until > first.lease_until
    # The first read finishes while two are still in flight: the lease stays.
    oldest = min(gates)
    gates[oldest].set()
    for _ in range(50):
        await asyncio.sleep(0.02)
        if len(sched._running.get(name, ())) == 2:
            break
    assert (await _job_row(name)).leased_by == sched.me
    # A place is free again, so the next due read starts at once.
    await asyncio.sleep(0.12)
    await sched._tick()
    assert len(sched._running[name]) == 3
    for _ in range(50):  # the new run has created its gate
        if len(gates) == 4:
            break
        await asyncio.sleep(0.02)
    for gate in list(gates.values()):
        gate.set()
    await asyncio.wait_for(asyncio.gather(*list(sched._running.get(name, ()))), timeout=5)
    await asyncio.sleep(0)
    runs = await _runs(name)
    assert [r.status for r in runs] == ["ok"] * 4
    assert [r.stats["overlapping"] for r in runs] == [0, 1, 2, 2]
    done = await _job_row(name)
    assert done.leased_by is None
    assert done.lease_until is None
    assert done.cursor == {"watermarks": {"4": "2026-09-28T16:00:00-04:00"}}  # never written
    assert done.last_run_id == runs[-1].id
    assert done.last_ok_at is not None


async def test_another_worker_cannot_join_an_overlapping_job() -> None:
    """Overlap is only within the worker holding the lease: another process still waits."""
    name = "edgar.poll_ownership"

    async def read(ctx: JobContext) -> None:
        await asyncio.sleep(10)

    job = Job(name, "edgar", read, Every(0.1), lease=timedelta(minutes=10), run_at_start=True, max_in_flight=3)
    sched = Scheduler([job])
    await sched.register()
    async with session_scope() as session:
        await session.execute(text(
            "UPDATE scheduler_jobs SET leased_by = 'other-host:1', lease_until = now() + interval '5 minutes' "
            "WHERE name = :name"), {"name": name})
    await sched._tick()
    assert name not in sched._running


@pytest.fixture
def two_processes() -> Iterator[tuple[int, int]]:
    """The ids of a process that is running (the stand-in for the live worker) and of one that
    has exited."""
    worker = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait(timeout=30)
    time.sleep(0.2)  # the test's leases are taken after the running process started
    try:
        yield worker.pid, gone.pid
    finally:
        worker.kill()
        worker.wait(timeout=30)


async def test_run_job_leaves_the_running_workers_leases_alone(two_processes: tuple[int, int]) -> None:
    """Pre-review 7: `python -m smt.cli run-job NAME` registers, and registering freed every
    lease held on this machine by another process, the live worker's too. Its run in flight
    then looked abandoned to the watchdog, and the job could start a second time. Now only a
    lease whose process has exited is taken back."""
    live_pid, dead_pid = two_processes
    ran: list[str] = []

    async def poll_144(ctx: JobContext) -> None:
        ran.append(ctx.job_name)

    host = Scheduler([]).me.rsplit(":", 1)[0]
    async with session_scope() as session:
        await session.execute(text(
            "INSERT INTO scheduler_jobs (name, lease_until, leased_by, next_run_at) VALUES "
            "('edgar.process_queue', now() + interval '9 minutes', :live, now() + interval '1 hour'), "
            "('edgar.reach_back_form4', now() + interval '9 minutes', :dead, now() + interval '1 hour'), "
            "('edgar.poll_form144', NULL, NULL, now() + interval '1 hour')"),
            {"live": f"{host}:{live_pid}", "dead": f"{host}:{dead_pid}"})
    before = await _job_row("edgar.process_queue")
    await run_job_once(Job("edgar.poll_form144", "edgar", poll_144, Every(60)))
    live = await _job_row("edgar.process_queue")
    assert live.leased_by == f"{host}:{live_pid}"       # the running worker keeps its lease
    assert live.lease_until == before.lease_until
    assert live.updated_at == before.updated_at           # so its run doesn't look abandoned
    assert (await _job_row("edgar.reach_back_form4")).leased_by is None   # an exited one: freed
    assert ran == ["edgar.poll_form144"]
    assert (await _job_row("edgar.poll_form144")).leased_by is None


@pytest.mark.skipif(os.name != "nt", reason="the creation-time check is Windows only (where ids are reused)")
async def test_a_lease_whose_process_id_was_reused_is_taken_back() -> None:
    """Windows reuses process ids. A process created after the lease was taken can't hold it."""
    sched = Scheduler([Job("edgar.process_queue", "edgar", _noop, Every(60))])
    host = sched.me.rsplit(":", 1)[0]
    holder = f"{host}:{os.getppid()}"  # a running process (pytest's parent), started before now
    async with session_scope() as session:
        await session.execute(text(
            "INSERT INTO scheduler_jobs (name, lease_until, leased_by, updated_at) VALUES "
            "('edgar.process_queue', now() + interval '9 minutes', :holder, now()), "
            "('edgar.reach_back_form4', now() + interval '9 minutes', :holder, now() - interval '20 years')"),
            {"holder": holder})
    await sched.register()
    assert (await _job_row("edgar.process_queue")).leased_by == holder      # taken while it ran: kept
    assert (await _job_row("edgar.reach_back_form4")).leased_by is None     # taken before it existed


async def test_a_run_does_not_release_a_lease_another_process_took_over() -> None:
    """A run that outlived its lease finishes after another process took the job: that
    process's lease must stay (the release used to clear whoever held it)."""
    name = "edgar.process_queue"
    took_over = asyncio.Event()

    async def slow(ctx: JobContext) -> None:
        async with session_scope() as session:
            await session.execute(text(
                "UPDATE scheduler_jobs SET leased_by = 'other-host:1', lease_until = now() + interval '5 minutes' "
                "WHERE name = :name"), {"name": name})
        took_over.set()

    sched = Scheduler([Job(name, "edgar", slow, Every(60), run_at_start=True)])
    await sched.register()
    await sched._tick()
    [task] = sched._running[name]
    await asyncio.wait_for(task, timeout=5)
    assert took_over.is_set()
    row = await _job_row(name)
    assert row.leased_by == "other-host:1"
    assert row.lease_until is not None
