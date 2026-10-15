"""A small asyncio scheduler with job leases stored in Postgres.

Why not APScheduler: every run already has to be recorded in Postgres
(`ingestion_runs`), leases must survive restarts, and long backfills need a
resumable cursor. Keeping that state in two places would be worse than
~200 lines here.

Guarantees:

* **One job never overlaps itself**, even across worker processes. A job
  runs only after its row in `scheduler_jobs` is leased with an atomic
  UPDATE ... WHERE lease is free. The one exception is a job registered with
  `max_in_flight` > 1 (the live feed reads, see `Job.max_in_flight`): its
  runs may overlap, but only inside the one worker process that holds the
  lease for all of them.
* **Every run is recorded** in `ingestion_runs`, whether it succeeds or fails.
* **Crash-safe.** An expired lease is simply taken over, and `next_run_at`
  and `cursor` live in the database.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import time
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError

from smt.db.models import IngestionRun, SchedulerJob
from smt.db.session import session_scope
from smt.logs import get_logger
from smt.worker.schedules import Schedule

log = get_logger("scheduler")

#: Pauses between retries while the database is unreachable (seconds, doubling).
DB_RETRY_FIRST = 5.0
DB_RETRY_MAX = 60.0


@dataclass
class JobContext:
    """What a running job receives: its run record, cursor and counters to fill in."""

    job_name: str
    run_id: int
    cursor: dict[str, Any]
    docs_fetched: int = 0
    rows_new: int = 0
    rows_updated: int = 0
    parse_failures: int = 0
    max_lag_seconds: float | None = None
    stats: dict[str, Any] = field(default_factory=dict)

    def note_lag(self, seconds: float) -> None:
        """Record a pipeline lag (detected_at - accepted_at) and keep the worst."""
        if self.max_lag_seconds is None or seconds > self.max_lag_seconds:
            self.max_lag_seconds = seconds


JobFunc = Callable[[JobContext], Awaitable[None]]


@dataclass(frozen=True)
class Job:
    """A named unit of work and when it runs."""

    name: str
    source: str
    func: JobFunc
    schedule: Schedule
    #: How long a lease lasts before another worker may take the job over.
    lease: timedelta = timedelta(minutes=15)
    #: Run immediately at worker start instead of waiting for `next_run_at`.
    run_at_start: bool = False
    #: How many runs of this job may be in flight at once in this worker (default: one).
    #:
    #: Why: the live Form 4 read must start on a fixed cadence even while an earlier read is
    #: still waiting for EDGAR. EDGAR takes 20-60 s to build a feed page at peak, and with runs
    #: in series one slow page held up every filing accepted meanwhile (critic round 6: 3 of 12
    #: Form 4s took over 120 s, e.g. 0001669531-26-000007 at 148.7 s). So a job like this is
    #: scheduled from the START of each run (a fixed cadence), and a due run starts while fewer
    #: than `max_in_flight` are running. The lease still keeps other worker processes out: the
    #: first run takes it, later runs extend it, and the last run to finish releases it. Such a
    #: job must keep no state in its cursor (overlapping runs would overwrite each other's), so
    #: its cursor is never written.
    max_in_flight: int = 1

    @property
    def overlaps(self) -> bool:
        """Whether runs of this job may overlap (see `max_in_flight`)."""
        return self.max_in_flight > 1


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


class Scheduler:
    """Runs registered jobs when they're due. One instance per worker process."""

    def __init__(self, jobs: list[Job], *, tick_seconds: float = 1.0) -> None:
        self.jobs = {job.name: job for job in jobs}
        self.tick_seconds = tick_seconds
        self.me = worker_id()
        #: The runs in flight, per job (more than one only for a job with `max_in_flight` > 1).
        self._running: dict[str, set[asyncio.Task[None]]] = {}
        #: When each run started (monotonic clock), to cancel runs that outlive their lease.
        self._started: dict[asyncio.Task[None], float] = {}
        #: Per overlapping job: held while a run takes or extends the lease, and while a finishing
        #: run decides whether it is the last one and releases the lease. Without it a run could
        #: start between a finishing run's "am I the last?" and its release, and then run unleased.
        self._lease_locks: dict[str, asyncio.Lock] = {}
        #: Runs of overlapping jobs whose work is over and which are recording their end.
        self._finishing: set[asyncio.Task[Any]] = set()
        self._stop = asyncio.Event()

    async def register(self) -> None:
        """Create a scheduler_jobs row for every job that doesn't have one yet, and take back
        leases left on this machine by a process that is no longer running.

        A worker restart gives the worker a new process id. Without this, live polling waited
        out the old process's lease (up to 10 minutes) after every restart (2026-09-24). Only
        leases of processes that have exited are taken back: `python -m smt.cli run-job`
        registers too, and it used to free every lease of the worker running beside it
        (pre-review 7). The worker's runs in flight then looked abandoned to the watchdog, and
        a job the worker was still running could start a second time. (Runs a dead process
        left unfinished are marked failed by the watchdog.)
        """
        host = self.me.rsplit(":", 1)[0]
        async with session_scope() as session:
            held = (await session.execute(text(
                "SELECT name, leased_by, updated_at FROM scheduler_jobs "
                "WHERE leased_by LIKE :host_prefix AND leased_by <> :me AND lease_until > now()"),
                {"host_prefix": host.replace("%", r"\%").replace("_", r"\_") + ":%", "me": self.me})).all()
            freed: list[str] = []
            for name, holder, taken in held:
                if _holder_running(holder, taken):
                    continue
                # `leased_by = :holder` again, in case the lease changed hands since the SELECT.
                await session.execute(text(
                    "UPDATE scheduler_jobs SET lease_until = NULL, leased_by = NULL, updated_at = now() "
                    "WHERE name = :name AND leased_by = :holder"), {"name": name, "holder": holder})
                freed.append(name)
            if freed:
                log.info("scheduler.leases_released", count=len(freed), jobs=sorted(freed))
            for job in self.jobs.values():
                stmt = insert(SchedulerJob).values(name=job.name).on_conflict_do_nothing()
                await session.execute(stmt)
                if job.run_at_start:
                    await session.execute(
                        update(SchedulerJob).where(SchedulerJob.name == job.name)
                        .values(next_run_at=text("now()"))
                    )

    async def run_forever(self) -> None:
        """Main loop: every tick, lease and start any job that is due.

        If the database goes away (a restart, a crash), the worker waits and
        retries with a growing pause (5 s up to 60 s) instead of exiting, so
        ingestion resumes by itself once Postgres is back. Jobs that were
        running when it went away lose their lease and simply run again.
        """
        registered = False
        pause = DB_RETRY_FIRST
        while not self._stop.is_set():
            try:
                if not registered:
                    await self.register()
                    registered = True
                    log.info("scheduler.started", worker=self.me, jobs=sorted(self.jobs))
                await self._tick()
                pause = DB_RETRY_FIRST
                wait = self.tick_seconds
            except (DBAPIError, OSError) as exc:
                log.error("scheduler.database_unavailable", error=str(exc).splitlines()[0][:300],
                          retry_in_seconds=pause)
                wait, pause = pause, min(pause * 2, DB_RETRY_MAX)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=wait)
        tasks = [task for runs in self._running.values() for task in runs]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def stop(self) -> None:
        self._stop.set()

    def _cancel_overruns(self) -> None:
        """Cancel runs that have outlived their lease, so the job can run again.

        A run can hang for good, for example on a network request made just before the
        machine slept (seen on 2026-09-25: the filing processor stood still for 7.5 hours).
        Once its lease is over another run may start anyway, so this one is cancelled.

        A run already being cancelled is left alone. This check runs every tick, and a second
        cancel used to land while the cancelled run was still writing its bookkeeping, so the
        run and the job's row were never updated and the job started again at once (critic
        round 6; it made one full test run fail).
        """
        now = time.monotonic()
        for name, runs in list(self._running.items()):
            for task in list(runs):
                started = self._started.get(task)
                if (started is None or task.done() or task.cancelling()
                        or now - started <= self.jobs[name].lease.total_seconds()):
                    continue
                log.error("job.overrun_cancelled", job=name, ran_seconds=round(now - started))
                task.cancel()

    async def _tick(self) -> None:
        self._cancel_overruns()
        for name in await self._due_jobs():
            job = self.jobs[name]
            if len(self._running.get(name, ())) >= job.max_in_flight:
                continue
            if job.overlaps:
                # Taking the lease and registering the run happen under the job's lock, so a run
                # finishing meanwhile sees this one and keeps the lease (see _bookkeep).
                async with self._lease_lock(name):
                    cursor = await self._try_lease(name)
                    if cursor is not None:
                        self._start(name, cursor)
                continue
            cursor = await self._try_lease(name)
            if cursor is not None:
                self._start(name, cursor)

    def _start(self, name: str, cursor: dict[str, Any]) -> None:
        """Start one run of a job whose lease is held, and track it until it ends."""
        overlapping = len(self._running.get(name, ()))
        task = asyncio.create_task(self._run(name, cursor, overlapping))
        self._running.setdefault(name, set()).add(task)
        self._started[task] = time.monotonic()
        task.add_done_callback(self._forget(name))

    def _lease_lock(self, name: str) -> asyncio.Lock:
        if name not in self._lease_locks:
            self._lease_locks[name] = asyncio.Lock()
        return self._lease_locks[name]

    def _forget(self, name: str) -> Callable[[asyncio.Task[None]], None]:
        """Callback that removes a finished run from the running set."""
        def done(task: asyncio.Task[None]) -> None:
            runs = self._running.get(name)
            if runs is not None:
                runs.discard(task)
                if not runs:
                    del self._running[name]
            self._started.pop(task, None)
            self._finishing.discard(task)
        return done

    async def _due_jobs(self) -> list[str]:
        async with session_scope() as session:
            rows = await session.execute(
                select(SchedulerJob.name).where(
                    SchedulerJob.enabled.is_(True),
                    SchedulerJob.next_run_at <= text("now()"),
                    SchedulerJob.name.in_(list(self.jobs)),
                )
            )
            return [r[0] for r in rows]

    async def _try_lease(self, name: str) -> dict[str, Any] | None:
        """Atomically take the job's lease. Returns its cursor, or None if someone else has it.

        For an overlapping job (`Job.max_in_flight` > 1), this worker may also extend a lease it
        already holds, and the next run is scheduled here, from this run's start, so reads keep a
        fixed cadence however long each one takes. Extending leaves `updated_at` alone: it records
        when the lease was taken, and the watchdog treats a run that started before it as
        abandoned (watchdog.check_leases)."""
        job = self.jobs[name]
        params: dict[str, Any] = {"secs": job.lease.total_seconds(), "me": self.me, "name": name}
        if not job.overlaps:
            sql = ("UPDATE scheduler_jobs SET lease_until = now() + make_interval(secs => :secs), "
                   "leased_by = :me, updated_at = now() "
                   "WHERE name = :name AND (lease_until IS NULL OR lease_until < now()) "
                   "RETURNING cursor")
        else:
            sql = ("UPDATE scheduler_jobs SET lease_until = now() + make_interval(secs => :secs), "
                   "next_run_at = :next_run, "
                   "updated_at = CASE WHEN leased_by = :me AND lease_until >= now() THEN updated_at "
                   "ELSE now() END, "
                   "leased_by = :me "
                   "WHERE name = :name AND (lease_until IS NULL OR lease_until < now() OR leased_by = :me) "
                   "RETURNING cursor")
            params["next_run"] = job.schedule.next_after(datetime.now(UTC))
        async with session_scope() as session:
            row = await session.execute(text(sql), params)
            got = row.first()
            return None if got is None else dict(got[0] or {})

    async def _run(self, name: str, cursor: dict[str, Any], overlapping: int = 0) -> None:
        job = self.jobs[name]
        this_run = asyncio.current_task()
        started = datetime.now(UTC)
        async with session_scope() as session:
            run = IngestionRun(job=name, source=job.source, status="running")
            session.add(run)
            await session.flush()
            run_id = run.id
        ctx = JobContext(job_name=name, run_id=run_id, cursor=cursor)
        if job.overlaps:
            # How many earlier runs were still in flight when this one started: the evidence that
            # a slow EDGAR page no longer holds up the next read.
            ctx.stats["overlapping"] = overlapping
        status, error = "ok", None
        try:
            await job.func(ctx)
            if ctx.parse_failures:
                status = "partial"
        except asyncio.CancelledError:
            status, error = "failed", "cancelled (the worker was shutting down, or the run outlived its lease)"
            raise
        except Exception as exc:
            status, error = "failed", "".join(traceback.format_exception(exc))[-4000:]
            log.error("job.failed", job=name, error=str(exc))
        finally:
            try:
                await _shielded(self._bookkeep(job, run_id, ctx, status, error, started, this_run))
            except (DBAPIError, OSError) as exc:
                # The database went away mid-run: the lease expires and the job runs again later.
                log.error("job.finish_failed", job=name, error=str(exc).splitlines()[0][:300])

    async def _bookkeep(self, job: Job, run_id: int, ctx: JobContext, status: str, error: str | None,
                        started: datetime, run: asyncio.Task[Any] | None) -> None:
        """Record a finished run. An overlapping job's lease is released only by its last run."""
        if not job.overlaps:
            await self._finish(job, run_id, ctx, status, error, started)
            return
        # Runs that are themselves only recording their end don't count as running: when several
        # finish together, each waits here for the lock, and if they counted one another none
        # would release the lease.
        if run is not None:
            self._finishing.add(run)
        async with self._lease_lock(job.name):
            others = [t for t in self._running.get(job.name, ())
                      if t is not run and not t.done() and t not in self._finishing]
            await self._finish(job, run_id, ctx, status, error, started, others_running=bool(others))

    async def _finish(self, job: Job, run_id: int, ctx: JobContext, status: str,
                      error: str | None, started: datetime, *, others_running: bool = False) -> None:
        async with session_scope() as session:
            await session.execute(
                update(IngestionRun).where(IngestionRun.id == run_id).values(
                    finished_at=text("now()"), status=status, error=error,
                    docs_fetched=ctx.docs_fetched, rows_new=ctx.rows_new,
                    rows_updated=ctx.rows_updated, parse_failures=ctx.parse_failures,
                    max_lag_seconds=ctx.max_lag_seconds, stats=ctx.stats,
                )
            )
            values: dict[str, Any]
            next_run: datetime | None
            if job.overlaps:
                # next_run_at was set when the run started (a fixed cadence) and the cursor isn't
                # used. Runs can finish out of order, so last_run_id only moves forward. The lease,
                # and updated_at (when it was taken), stay as they are while other runs go on.
                next_run = None
                values = {"last_run_id": func.greatest(func.coalesce(SchedulerJob.last_run_id, 0), run_id)}
                if not others_running:
                    values.update(self._release_lease())
            else:
                next_run = job.schedule.next_after(started)
                values = {"next_run_at": next_run, "last_run_id": run_id, "cursor": ctx.cursor,
                          **self._release_lease()}
            if status in ("ok", "partial"):
                values["last_ok_at"] = text("now()")
            await session.execute(update(SchedulerJob).where(SchedulerJob.name == job.name).values(**values))
        log.info("job.finished", job=job.name, status=status, new=ctx.rows_new,
                 fetched=ctx.docs_fetched, failures=ctx.parse_failures,
                 next_run=next_run.isoformat() if next_run else None)


    def _release_lease(self) -> dict[str, Any]:
        """The scheduler_jobs values that free the job's lease, but only a lease this process
        holds. A run that outlived its lease, after which another process took the job, must not
        free that process's lease: the job could then start once more while it is still running.
        """
        mine = SchedulerJob.leased_by == self.me
        return {
            "lease_until": case((mine, None), else_=SchedulerJob.lease_until),
            "leased_by": case((mine, None), else_=SchedulerJob.leased_by),
            # updated_at records when the lease was taken (watchdog.check_leases reads it), so it
            # stays as it is when the lease is someone else's.
            "updated_at": case((mine, func.now()), else_=SchedulerJob.updated_at),
        }


def _holder_running(holder: str, taken: datetime | None) -> bool:
    """Whether the process named by a lease holder "hostname:pid" on this machine still runs.

    `taken` is when the lease was taken (scheduler_jobs.updated_at). A process created after
    that can't be the holder: Windows reuses process ids, and an unrelated process that got
    the old worker's id would otherwise keep a dead worker's lease until it expired."""
    pid_text = holder.rsplit(":", 1)[-1]
    if not pid_text.isdigit():
        return True                          # not a holder this code wrote: leave it alone
    pid = int(pid_text)
    if os.name == "nt":
        created = _windows_process_created(pid)
        return created is not None and (taken is None or created <= taken)
    try:
        os.kill(pid, 0)                      # signal 0 only checks that the process exists
    except ProcessLookupError:
        return False
    except PermissionError:
        return True                          # it exists, under another user
    return True


def _windows_process_created(pid: int) -> datetime | None:
    """When a running Windows process was created, or None when no running process has this id.
    A process that exists but can't be inspected counts as created long ago (so as the holder).

    Never os.kill() on Windows: there it terminates the process whatever the signal."""
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    still_active = 259
    error_access_denied = 5
    long_ago = datetime.min.replace(tzinfo=UTC)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined,unused-ignore]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    # Declared so the HANDLE is passed at its full width, not as a C int.
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return long_ago if ctypes.get_last_error() == error_access_denied else None  # type: ignore[attr-defined,unused-ignore]
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != still_active:
            return None
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                        ctypes.byref(kernel), ctypes.byref(user)):
            return long_ago
        ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime   # 100 ns units since 1601
        return datetime(1601, 1, 1, tzinfo=UTC) + timedelta(microseconds=ticks // 10)
    finally:
        kernel32.CloseHandle(handle)


async def _shielded(work: Awaitable[None]) -> None:
    """Await `work` to the end even if the calling task is cancelled meanwhile.

    A run's bookkeeping (its ingestion_runs row, the job's next run and lease) must be written
    whatever happens: interrupted halfway, the job looks as if it were still running and starts
    again at once. A cancellation that arrives meanwhile is passed on once the work is done."""
    finishing = asyncio.ensure_future(work)
    interrupted = False
    while True:
        try:
            await asyncio.shield(finishing)
            break
        except asyncio.CancelledError:
            if finishing.done():
                raise  # the work itself was cancelled: there is nothing left to wait for
            interrupted = True
    if interrupted:
        raise asyncio.CancelledError


async def run_job_once(job: Job) -> JobContext:
    """Run one job immediately, outside the loop (CLI `smt run-job`). Still recorded."""
    scheduler = Scheduler([job])
    await scheduler.register()
    cursor = await scheduler._try_lease(job.name)
    if cursor is None:
        raise RuntimeError(f"job {job.name} is currently leased by another worker")
    started = datetime.now(UTC)
    async with session_scope() as session:
        run = IngestionRun(job=job.name, source=job.source, status="running")
        session.add(run)
        await session.flush()
        run_id = run.id
    ctx = JobContext(job_name=job.name, run_id=run_id, cursor=cursor)
    status, error = "ok", None
    try:
        await job.func(ctx)
    except Exception as exc:
        status, error = "failed", "".join(traceback.format_exception(exc))[-4000:]
        raise
    finally:
        await scheduler._finish(job, run_id, ctx, status, error, started)
    return ctx
