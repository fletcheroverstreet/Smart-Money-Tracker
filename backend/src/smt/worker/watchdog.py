"""The watchdog: notices when the pipeline stops doing its job, and says so once.

Every five minutes it looks at the database and asks:

1. **Stale sources.** Has each EDGAR feed had a successful poll recently?
   During EDGAR hours (06:00-22:00 ET on SEC business days) the feeds are
   polled at least every minute, so WATCHDOG_STALE_MINUTES (default 15) without a
   success is a problem. Outside those hours they're polled every 10 minutes,
   so the limit is 45 minutes. The filing processor and the daily jobs have
   their own limits.
2. **The EDGAR queue.** Are discovered filings piling up or waiting too long,
   and has any filing been given up on? Every failed filing we still don't
   hold is reported, by cause: EDGAR didn't answer, the SEC refused our
   requests, or our database was busy, until the filing had waited 3 days;
   the document couldn't be read; or some other error after the maximum
   number of attempts.
3. **Quarantine.** Were any documents set aside for review recently?
4. **Leases.** Has a job held its lease past the time limit (stuck, or its
   worker stopped)? Runs left "running" by a worker that stopped are closed
   here and recorded as not finished.
5. **Failed runs.** Is a job's latest run failing (three in a row for jobs
   that run every minute or so, one for daily jobs)?
6. **Stale prices.** Are the daily closes of recently traded securities
   more than two trading sessions behind (counted per security)?

Each problem becomes one row in `watchdog_findings`. It alerts once when it
starts and once when it ends (smt.alerts.dispatch); while it stays open, its
title, detail and numbers are rewritten on every run, but nothing is re-sent.
Checks with a known start-up race (leases) must see the same problem on two
runs in a row before it counts.

Findings are shown on the public /status page, so their text is plain
sentences (smt.worker.error_text): no Python class names, URLs or machine
names. The technical detail stays in the run records and the worker's log.

What it can't see: the watchdog runs inside the worker, so if the whole worker
stops, nothing here runs. The worker heartbeat on /status covers that case.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from smt.alerts.dispatch import clear_alert_for, dispatch_alerts, initial_open_alert
from smt.alerts.notifier import notifier_from_settings
from smt.alerts.wording import count_text, duration_text, when_et
from smt.config import get_settings
from smt.db.models import SchedulerJob, WatchdogFinding
from smt.db.session import session_scope
from smt.logs import get_logger
from smt.sources.edgar.pipeline import MAX_ATTEMPTS, TRANSIENT_GIVE_UP
from smt.worker.error_text import (
    QUEUE_ERROR_CLASS_SQL,
    FailureCause,
    failure_cause,
    plain_alert_error,
    plain_error,
    plain_reason,
)
from smt.worker.scheduler import Job, JobContext, worker_id
from smt.worker.schedules import DailyAt, EdgarHours

log = get_logger("watchdog")

#: Plain names for the jobs the watchdog talks about.
JOB_LABELS: dict[str, str] = {
    "edgar.poll_ownership": "EDGAR Form 4 feed",
    "edgar.poll_forms_3_5": "EDGAR Forms 3 and 5 feed",
    "edgar.poll_form144": "EDGAR Form 144 feed",
    "edgar.reach_back_form4": "EDGAR Form 4 feed catch-up",
    "edgar.reach_back_other": "EDGAR Forms 3, 5 and 144 feed catch-up",
    "edgar.process_queue": "EDGAR filing processor",
    "edgar.reconcile_daily_index": "nightly EDGAR daily-index check",
    "edgar.fill_gaps": "EDGAR missing-dates check",
    "edgar.load_new_dataset": "SEC quarterly data set load",
    "edgar.expire_planned_sales": "planned-sale follow-up",
    "edgar.refresh_company_tickers": "SEC ticker list refresh",
    "edgar.refresh_submissions": "SEC company profile refresh",
    "edgar.verify_names": "EDGAR name check",
    "edgar.backfill_insider_dataset": "Forms 3/4/5 history backfill",
    "quality.flag_transactions": "check for figures that can't be right",
    "quality.repair_amendments": "amendment pairing",
    "people.refresh_stats": "People directory recount",
    "prices.refresh_daily": "daily price refresh",
    "worker.heartbeat": "worker heartbeat",
    "worker.watchdog": "watchdog",
}

#: The live EDGAR polls. Their limit depends on EDGAR hours (see module docstring).
POLL_JOBS = ("edgar.poll_ownership", "edgar.poll_forms_3_5", "edgar.poll_form144")
#: Outside EDGAR hours the polls run every 10 minutes; this much silence is a problem.
IDLE_STALE_AFTER = timedelta(minutes=45)
#: Other jobs: (how long without a success is a problem, severity).
OTHER_LIMITS: dict[str, tuple[timedelta, str]] = {
    "edgar.process_queue": (timedelta(minutes=15), "warning"),
    # The feed catch-up runs every 1-2 minutes in EDGAR hours and every 10 minutes otherwise.
    "edgar.reach_back_form4": (IDLE_STALE_AFTER, "warning"),
    "edgar.reach_back_other": (IDLE_STALE_AFTER, "warning"),
    "edgar.reconcile_daily_index": (timedelta(hours=26), "warning"),
    "edgar.refresh_company_tickers": (timedelta(hours=26), "warning"),
    "edgar.refresh_submissions": (timedelta(hours=26), "warning"),
    "edgar.verify_names": (timedelta(hours=26), "warning"),
}

#: EDGAR queue: open a backlog finding when the oldest due filing has waited this long...
QUEUE_WAIT_OPEN = timedelta(minutes=30)
#: ...or when this many are pending and the number grew since the last check.
QUEUE_PENDING_OPEN = 500
#: An open backlog finding clears only below both of these (so it doesn't flap).
QUEUE_WAIT_CLEAR = timedelta(minutes=10)
QUEUE_PENDING_CLEAR = 250

#: Quarantine rows newer than this keep the "quarantine" finding open.
QUARANTINE_WINDOW = timedelta(hours=6)
#: A lease must be this far past its end before it counts (the scheduler ticks every second).
LEASE_GRACE = timedelta(minutes=2)
#: Failed runs older than this are not reported.
FAILED_RUNS_WINDOW = timedelta(days=7)
#: Consecutive failures before a frequently running job counts as failing.
FAILURES_BEFORE_ALERT = 3


# ---------------------------------------------------------------- data shapes
@dataclass(frozen=True)
class Draft:
    """A problem one check sees right now (before it's matched to stored findings)."""

    key: str
    check: str
    severity: str
    title: str
    detail: str = ""
    subject: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    #: condition (stays open while it holds) | event (recorded already closed)
    kind: str = "condition"


@dataclass
class CheckResult:
    """Everything one check saw. Open findings of this check that it no longer sees get cleared."""

    check: str
    drafts: list[Draft] = field(default_factory=list)
    #: How many runs in a row a condition must be seen before it's recorded.
    confirm_after: int = 1


@dataclass
class WatchContext:
    """What every check needs: the time, what's already open, the job registry, memory between runs."""

    now: datetime
    jobs: dict[str, Job]
    open_keys: set[str]
    #: The watchdog job's cursor (persisted by the scheduler between runs).
    cursor: dict[str, Any]
    stale_after: timedelta


@dataclass
class Summary:
    """Counts for the run record."""

    opened: int = 0
    cleared: int = 0
    still_open: int = 0
    events: int = 0
    check_errors: list[str] = field(default_factory=list)

    def as_stats(self) -> dict[str, Any]:
        return {"opened": self.opened, "cleared": self.cleared, "still_open": self.still_open,
                "events": self.events, "check_errors": self.check_errors}


def label(job: str) -> str:
    return JOB_LABELS.get(job, job)


def _were(n: int) -> str:
    return "was" if n == 1 else "were"


# ---------------------------------------------------------------- checks
async def check_stale_sources(session: AsyncSession, wc: WatchContext) -> list[CheckResult]:
    """No successful run for longer than the job's limit."""
    names = [n for n in (*POLL_JOBS, *OTHER_LIMITS) if n in wc.jobs]
    result = CheckResult("stale_source")
    if not names:
        return [result]
    rows = (await session.execute(
        select(SchedulerJob.name, SchedulerJob.enabled, SchedulerJob.last_ok_at)
        .where(SchedulerJob.name.in_(names)))).all()
    # A job that has never succeeded is measured from its first recorded attempt.
    first_runs: dict[str, datetime] = {
        job: started for job, started in (await session.execute(text(
            "SELECT job, min(started_at) FROM ingestion_runs WHERE job = ANY(:names) GROUP BY job"),
            {"names": names})).tuples().all()
    }
    edgar_hours = EdgarHours().is_active(wc.now)
    for name, enabled, last_ok in rows:
        if not enabled:
            continue
        since = last_ok or first_runs.get(name)
        if since is None:
            continue  # registered but never started yet: nothing to judge
        if name in POLL_JOBS:
            limit = wc.stale_after if edgar_hours else max(wc.stale_after, IDLE_STALE_AFTER)
            severity = "critical"
            expected = ("It runs at least every minute during EDGAR hours (6 AM to 10 PM ET on SEC business days)."
                        if edgar_hours else "Outside EDGAR hours it runs every 10 minutes.")
        else:
            limit, severity = OTHER_LIMITS[name]
            expected = f"It is expected to succeed at least every {duration_text(limit)}."
        age = wc.now - since
        if age <= limit:
            continue
        what = "poll of the " if name in POLL_JOBS else "run of the "
        last = (f"The last success was at {when_et(last_ok)}." if last_ok
                else f"It has not succeeded since its first attempt at {when_et(since)}.")
        result.drafts.append(Draft(
            key=f"stale_source:{name}", check="stale_source", severity=severity, subject=name,
            title=f"No successful {what}{label(name)} in {duration_text(age)}.",
            detail=f"{last} {expected}",
            data={"job": name, "last_ok_at": last_ok.isoformat() if last_ok else None,
                  "minutes_since": round(age.total_seconds() / 60),
                  "limit_minutes": round(limit.total_seconds() / 60), "edgar_hours": edgar_hours},
        ))
    return [result]


@dataclass
class _FailedGroup:
    """Failed queue items that share one cause (see error_text.failure_cause)."""

    count: int = 0
    latest_at: datetime | None = None
    latest_error: str | None = None
    #: Up to 10 (accession, form type) examples; the first is the newest.
    examples: list[tuple[str, str]] = field(default_factory=list)


#: The order the queue findings are listed in.
_CAUSE_ORDER: tuple[FailureCause, ...] = ("outage", "refused", "busy", "unreadable", "other")
#: Each cause's finding key. "gave_up" predates the split into causes and is kept, so a
#: finding already open under it stays the same finding.
_CAUSE_KEYS: dict[FailureCause, str] = {
    "outage": "edgar_queue:outage", "refused": "edgar_queue:refused", "busy": "edgar_queue:busy",
    "unreadable": "edgar_queue:unreadable", "other": "edgar_queue:gave_up",
}


def _them(n: int) -> str:
    return "it" if n == 1 else "them"


def _latest(group: _FailedGroup, reason: str | None = None) -> str:
    """'Latest: <accession> (Form 4). <reason> ', naming the accession only once: when the
    reason already names it, this becomes 'Latest (Form 4): <reason> '."""
    if not group.examples:
        return f"{reason} " if reason else ""
    accession, form_type = group.examples[0]
    if reason and accession in reason:
        return f"Latest (Form {form_type}): {reason} "
    return f"Latest: {accession} (Form {form_type}). " + (f"{reason} " if reason else "")


def _failed_draft(cause: FailureCause, group: _FailedGroup) -> Draft:
    """One finding per cause, in plain words, with the remedy: the owner runs `smt queue retry`
    (smt.cli), which puts every failed filing back in line with a fresh set of attempts.

    The three transient causes (outage, refused, busy) never use up an attempt: the pipeline
    retries such a filing every few minutes and sets it aside only once it has waited
    TRANSIENT_GIVE_UP since it was found (smt.sources.edgar.pipeline.process_queue)."""
    n = group.count
    what = count_text(n, "EDGAR filing")
    retry = f"The owner can put {_them(n)} back in line"
    waited = duration_text(TRANSIENT_GIVE_UP)
    rule = f"Such filings are retried every few minutes and set aside once they have waited {waited}."
    if cause == "outage":
        # FetchError covers both an SEC outage (5xx, 429, no answer) and a lost connection
        # here (the address lookup failing, say); the latest filing's reason tells which.
        title = f"{what} couldn't be fetched for {waited} and {_were(n)} set aside."
        detail = (f"EDGAR didn't answer, or the connection to it failed. {rule} "
                  f"{_latest(group, plain_error(group.latest_error))}{retry} once EDGAR answers again.")
    elif cause == "refused":
        # The SEC answers 403 to requests that come too fast or give no contact address in the
        # User-Agent (smt.net.client pauses all SEC requests when it sees one). Retrying without
        # checking the setting would just be refused again.
        title = f"{what} couldn't be fetched because the SEC refused our requests (HTTP 403), and {_were(n)} set aside."
        detail = (f"The SEC refuses requests that come too fast or don't give a contact address in the "
                  f"User-Agent. {rule} {_latest(group)}"
                  f"The owner can check the contact address the site sends, then put {_them(n)} back in line.")
    elif cause == "busy":
        # A deadlock or serialization failure: our own database, not EDGAR.
        title = f"{what} couldn't be saved because the database was busy, and {_were(n)} set aside."
        detail = (f"The database cancels a load when another update to the same rows is running at the same "
                  f"time. {rule} {_latest(group)}{retry}.")
    elif cause == "unreadable":
        title = f"{what} couldn't be read and {_were(n)} set aside."
        detail = (f"EDGAR sent {_them(n)}, but the document wasn't in a format the reader understands. "
                  f"{_latest(group)}{retry} once the reader is fixed.")
    else:
        title = f"{what} couldn't be loaded after {MAX_ATTEMPTS} tries and {_were(n)} set aside."
        detail = f"{_latest(group, plain_error(group.latest_error))}{retry}."
    return Draft(
        key=_CAUSE_KEYS[cause], check="edgar_queue", severity="warning", subject="edgar.process_queue",
        title=title, detail=detail,
        data={"count": n, "cause": cause,
              "examples": [{"accession": a, "form_type": f} for a, f in group.examples]},
    )


async def check_edgar_queue(session: AsyncSession, wc: WatchContext) -> list[CheckResult]:
    """Discovered filings piling up or waiting too long; every filing given up on, by cause."""
    result = CheckResult("edgar_queue")
    row = (await session.execute(text(
        "SELECT count(*) FILTER (WHERE status = 'pending'), "
        "count(*) FILTER (WHERE status = 'pending' AND next_attempt_at <= :now), "
        "min(next_attempt_at) FILTER (WHERE status = 'pending' AND next_attempt_at <= :now) "
        "FROM edgar_queue"), {"now": wc.now})).one()
    pending, due, oldest_due = int(row[0]), int(row[1]), row[2]
    wait = wc.now - oldest_due if oldest_due else timedelta(0)
    previous = wc.cursor.get("queue_pending")
    growing = isinstance(previous, int) and pending > previous
    wc.cursor["queue_pending"] = pending

    key = "edgar_queue:backlog"
    if key in wc.open_keys:
        backlog = wait >= QUEUE_WAIT_CLEAR or pending >= QUEUE_PENDING_CLEAR
    else:
        backlog = wait >= QUEUE_WAIT_OPEN or (pending >= QUEUE_PENDING_OPEN and growing)
    if backlog:
        if wait >= QUEUE_WAIT_OPEN:
            title = f"EDGAR filings are waiting too long: the oldest has been due for {duration_text(wait)}."
        else:
            title = f"{count_text(pending, 'EDGAR filing')} are waiting to be processed."
        trend = f" At the previous check there were {previous:,}." if isinstance(previous, int) else ""
        result.drafts.append(Draft(
            key=key, check="edgar_queue", severity="warning", subject="edgar.process_queue", title=title,
            detail=(f"{count_text(pending, 'filing')} pending, {due:,} due now.{trend} "
                    "The processor normally clears the queue every 15 seconds."),
            data={"pending": pending, "due": due, "previous_pending": previous,
                  "oldest_due_minutes": round(wait.total_seconds() / 60)},
        ))

    # Every failed item counts, whatever its attempt count: a filing given up on after
    # TRANSIENT_GIVE_UP of SEC outages has its attempts refunded (pipeline.process_queue), so
    # it can sit below MAX_ATTEMPTS. The class its stored error starts with tells the causes apart;
    # grouping in SQL keeps this cheap even after a long outage leaves thousands.
    # A failed item whose filing we now hold is not a problem: the SEC's quarterly data set
    # (sources/edgar/backfill.py) loads filings by accession without touching the queue, and
    # enqueue() likewise treats a held accession as needing no queue work.
    # Every array_agg sorts the same way (newest first, then by accession), so the first
    # error and the first accession in a group belong to the same filing.
    failed = (await session.execute(text(
        "SELECT substring(coalesce(q.last_error, '') from :class_re) AS error_class, count(*) AS n, "
        "max(q.discovered_at) AS latest_at, "
        "(array_agg(q.last_error ORDER BY q.discovered_at DESC NULLS LAST, q.accession DESC))[1] AS latest_error, "
        "(array_agg(q.accession ORDER BY q.discovered_at DESC NULLS LAST, q.accession DESC))[1:10] AS accessions, "
        "(array_agg(q.form_type ORDER BY q.discovered_at DESC NULLS LAST, q.accession DESC))[1:10] AS forms "
        "FROM edgar_queue q WHERE q.status = 'failed' "
        "AND NOT EXISTS (SELECT 1 FROM filings f WHERE f.source = 'edgar' AND f.source_key = q.accession) "
        "GROUP BY 1 ORDER BY max(q.discovered_at) DESC NULLS LAST, max(q.accession) DESC"),
        {"class_re": QUEUE_ERROR_CLASS_SQL})).all()
    groups: dict[FailureCause, _FailedGroup] = {}
    for f in failed:  # newest group first, so each cause's first group holds its latest filing
        group = groups.setdefault(failure_cause(f.error_class), _FailedGroup())
        if group.latest_at is None:
            group.latest_at, group.latest_error = f.latest_at, f.latest_error
        group.count += int(f.n)
        group.examples = (group.examples + list(zip(f.accessions, f.forms, strict=True)))[:10]
    for cause in _CAUSE_ORDER:
        if cause in groups:
            result.drafts.append(_failed_draft(cause, groups[cause]))
    return [result]


async def check_quarantine(session: AsyncSession, wc: WatchContext) -> list[CheckResult]:
    """Documents set aside for review in the last few hours (the quarantine is growing)."""
    result = CheckResult("quarantine")
    since = wc.now - QUARANTINE_WINDOW
    recent, total = (await session.execute(text(
        "SELECT count(*) FILTER (WHERE created_at > :since), count(*) "
        "FROM quarantine WHERE resolved_at IS NULL"), {"since": since})).one()
    if not recent:
        return [result]
    examples = (await session.execute(text(
        "SELECT id, source, filing_ref, reason, created_at FROM quarantine "
        "WHERE resolved_at IS NULL AND created_at > :since ORDER BY id DESC LIMIT 5"),
        {"since": since})).all()
    latest = examples[0]
    result.drafts.append(Draft(
        key="quarantine:new", check="quarantine", severity="warning", subject="quarantine",
        title=(f"{count_text(int(recent), 'document')} set aside for review (quarantined) "
               f"in the last {duration_text(QUARANTINE_WINDOW)}."),
        detail=(f"They failed validation and were kept, not dropped. {int(total):,} open in total. "
                f"Latest: {latest.filing_ref or latest.source}: {plain_reason(latest.reason)}"),
        data={"recent": int(recent), "open_total": int(total), "examples": [
            {"id": e.id, "source": e.source, "filing_ref": e.filing_ref, "reason": plain_reason(e.reason),
             "created_at": e.created_at.isoformat()} for e in examples]},
    ))
    return [result]


async def check_leases(session: AsyncSession, wc: WatchContext) -> list[CheckResult]:
    """Jobs holding a lease past its end, and runs a stopped worker left 'running'."""
    leases = CheckResult("lease", confirm_after=2)
    expired = (await session.execute(
        select(SchedulerJob.name, SchedulerJob.leased_by, SchedulerJob.lease_until, SchedulerJob.updated_at)
        .where(SchedulerJob.leased_by.is_not(None), SchedulerJob.lease_until < wc.now - LEASE_GRACE)
    )).all()
    me = worker_id()
    for name, holder, until, leased_at in expired:
        # The holder is "hostname:pid". It names the owner's machine, so it goes to the log only.
        log.warning("watchdog.lease_expired", job=name, leased_by=holder, lease_until=until.isoformat())
        if holder == me:
            who = (f"It was started at {when_et(leased_at)} by this worker, which is still running, "
                   "so the job itself may be stuck.")
        else:
            # Another process holds it. Normally only one worker runs, so that one has stopped
            # (a restart gives the worker a new process id); if a second worker is running, it
            # may instead be stuck on this job.
            who = (f"It was started at {when_et(leased_at)} by a worker that has since stopped "
                   "(or is stuck on this job).")
        leases.drafts.append(Draft(
            key=f"lease:{name}", check="lease", severity="warning", subject=name,
            title=f"The {label(name)} has run past its time limit (lease ended {duration_text(wc.now - until)} ago).",
            detail=who,
            data={"job": name, "lease_until": until.isoformat(), "same_worker": holder == me},
        ))

    # A run is abandoned when it is still marked running but its job has been
    # released since (no lease), or re-leased after the run started. One job
    # never runs twice at once, so such a run can't still be going.
    orphans = (await session.execute(text(
        "SELECT r.id, r.job, r.started_at FROM ingestion_runs r JOIN scheduler_jobs j ON j.name = r.job "
        "WHERE r.status = 'running' AND r.started_at < :cutoff "
        "AND (j.leased_by IS NULL OR r.started_at < j.updated_at) ORDER BY r.id"),
        {"cutoff": wc.now - LEASE_GRACE})).all()
    events = CheckResult("abandoned_runs")
    if orphans:
        ids = [o.id for o in orphans]
        await session.execute(text(
            "UPDATE ingestion_runs SET status = 'failed', finished_at = :now, error = :error "
            "WHERE id = ANY(:ids) AND status = 'running'"),
            {"now": wc.now, "ids": ids, "error": (
                "Did not finish: the worker stopped during this run. "
                "Closed by the watchdog.")})
        jobs = sorted({o.job for o in orphans})
        events.drafts.append(Draft(
            key=f"abandoned_runs:{ids[0]}", check="abandoned_runs", severity="info", kind="event",
            title=(f"{count_text(len(ids), 'job run')} did not finish because the worker stopped "
                   f"({', '.join(label(j) for j in jobs)})."),
            detail=(f"{'It is' if len(ids) == 1 else 'They are'} now recorded as not finished. "
                    "Each job runs again on its normal schedule."),
            data={"run_ids": ids, "jobs": jobs},
        ))
    return [leases, events]


async def check_failed_runs(session: AsyncSession, wc: WatchContext) -> list[CheckResult]:
    """A job whose latest runs failed (3 in a row for frequent jobs, 1 for daily and one-off jobs)."""
    result = CheckResult("job_failed")
    rows = (await session.execute(text(
        "SELECT job, status, error, started_at FROM ("
        "  SELECT job, status, error, started_at, "
        "         row_number() OVER (PARTITION BY job ORDER BY started_at DESC, id DESC) AS rn "
        "  FROM ingestion_runs WHERE status <> 'running' AND started_at > :since) t "
        "WHERE rn <= :depth ORDER BY job, started_at DESC"),
        {"since": wc.now - FAILED_RUNS_WINDOW, "depth": FAILURES_BEFORE_ALERT})).all()
    by_job: dict[str, list[Any]] = {}
    for r in rows:
        by_job.setdefault(r.job, []).append(r)
    for job, runs in by_job.items():
        streak = 0
        for r in runs:
            if r.status != "failed":
                break
            streak += 1
        registered = wc.jobs.get(job)
        needed = FAILURES_BEFORE_ALERT if registered and not isinstance(registered.schedule, DailyAt) else 1
        if streak < needed:
            continue
        latest = runs[0]
        # The stored error is a traceback; the page gets one plain sentence (error_text).
        reason = plain_error(latest.error)
        throttled = "SecThrottled" in (latest.error or "") or "SEC returned 403" in (latest.error or "")
        runs_text = "run" if streak == 1 else f"{streak} runs"
        detail = f"Latest failure at {when_et(latest.started_at)}. {reason}"
        if throttled:
            detail += (" The SEC answers 403 when it gets too many requests or no User-Agent. "
                       "All SEC requests pause for 11 minutes.")
        result.drafts.append(Draft(
            key=f"job_failed:{job}", check="job_failed", severity="critical" if throttled else "warning",
            subject=job, title=f"The last {runs_text} of the {label(job)} failed.", detail=detail,
            data={"job": job, "consecutive_failures": streak, "last_error": reason,
                  "last_failed_at": latest.started_at.isoformat()},
        ))
    return [result]


#: Daily closes may lag the market by this many trading sessions before it's a finding (the
#: provider posts a session's closes some hours after it ends).
PRICES_BEHIND_SESSIONS = 2


def _last_closed_session(now: datetime) -> date:
    """The most recent trading day whose closing prices could be out (after 6 PM ET on that day)."""
    from smt.domain.business_days import is_trading_day, to_eastern

    local = to_eastern(now)
    day = local.date() if local.hour >= 18 else local.date() - timedelta(days=1)
    while not is_trading_day(day):
        day -= timedelta(days=1)
    return day


#: The securities whose prices are watched: those an insider traded within this many days, the
#: trades the closing-price yardstick is checking.
PRICES_TRADED_DAYS = 90
#: The finding opens when more than this share of those securities (the ones with stored closes)
#: are behind. A few always are: a delisted or renamed ticker stops getting closes (3 of 475 on
#: 2026-09-29), and they alone mustn't hold the finding open for good.
PRICES_BEHIND_SHARE = 0.05


def _sessions_between(last_close: date, last_session: date) -> int:
    """Trading sessions after `last_close`, up to and including `last_session`."""
    from smt.domain.business_days import is_trading_day

    behind, day = 0, last_close + timedelta(days=1)
    while day <= last_session:
        behind += is_trading_day(day)
        day += timedelta(days=1)
    return behind


def _long_date(day: date) -> str:
    """'Sep 18, 2026'."""
    return f"{day:%b} {day.day}, {day.year}"


async def check_stale_prices(session: AsyncSession, wc: WatchContext) -> list[CheckResult]:
    """Daily closes more than PRICES_BEHIND_SESSIONS trading sessions old, measured per security
    over the securities insiders traded in the last PRICES_TRADED_DAYS days.

    Measured from the prices themselves, not the price job's status: a run whose provider stopped
    updating once finished "partial" and still counted as a success, so the stale-source check
    never saw it (every series stopped at 2026-09-18 for ten days without a finding).

    Measured per security, not from the newest close of any series: with that rule one refreshed
    series cleared the finding for every series still behind. Only securities an insider traded
    in the last PRICES_TRADED_DAYS days count, because those are the trades the prices check. The
    183 series that stop at 2026-08-13 are preferred shares and second share classes (USB-PA,
    BRK-A, JPM-PC ...) with no insider trade at all (2026-09-29), so they're left out on purpose.
    Securities with no stored closes at all (no provider has the ticker) aren't behind; the price
    job counts them as unavailable. Prices aren't shown on public pages; they are a yardstick for
    trade values that can't be right."""
    from smt.domain.business_days import to_eastern

    result = CheckResult("prices_stale")
    if "prices.refresh_daily" not in wc.jobs:
        return [result]
    since = to_eastern(wc.now).date() - timedelta(days=PRICES_TRADED_DAYS)
    # How many recently traded securities have their newest close on each day.
    stops = (await session.execute(text("""
        WITH traded AS (
            SELECT DISTINCT t.security_id FROM transactions t
            WHERE t.traded_on >= :since AND t.is_current AND t.security_id IS NOT NULL
        )
        SELECT n.last_close, count(*) AS securities
        FROM (SELECT p.security_id, max(p.day) AS last_close
              FROM prices_daily p JOIN traded x ON x.security_id = p.security_id
              GROUP BY p.security_id) n
        GROUP BY n.last_close"""), {"since": since})).all()
    measured = sum(int(r.securities) for r in stops)
    last_session = _last_closed_session(wc.now)
    behind = [(r.last_close, int(r.securities), _sessions_between(r.last_close, last_session)) for r in stops]
    behind = [b for b in behind if b[2] > PRICES_BEHIND_SESSIONS]
    lagging = sum(n for _, n, _ in behind)
    if not lagging or lagging <= measured * PRICES_BEHIND_SHARE:
        return [result]
    # The date most of the lagging series stop at (the latest, on a tie): usually the day the
    # provider stopped updating.
    common, common_n, common_behind = max(behind, key=lambda b: (b[1], b[0]))
    # Worded for the counts it states. Before pre-review 7 the live finding read "for 475 of 475
    # recently traded securities" (meaning all of them) and a single lagging series read "They
    # all stop at".
    if common_n == lagging:
        where = f"{'It stops' if lagging == 1 else 'They all stop'} at {_long_date(common)}"
    else:
        where = f"{common_n:,} of them {'stops' if common_n == 1 else 'stop'} at {_long_date(common)}"
    if lagging < measured:
        whom = f"{lagging:,} of {count_text(measured, 'recently traded security', 'recently traded securities')}"
    elif measured == 1:
        whom = "the one recently traded security"
    else:
        whom = f"all {measured:,} recently traded securities"
    result.drafts.append(Draft(
        key="prices_stale", check="prices_stale", severity="warning", subject="prices.refresh_daily",
        title=f"Daily prices are more than {PRICES_BEHIND_SESSIONS} trading sessions behind for {whom}.",
        detail=(f"{where}, {common_behind} trading sessions ago. The price provider hasn't delivered newer "
                "closes, so the checks that compare a trade's price with the market see no recent prices "
                "for them. Prices aren't shown on public pages."),
        data={"securities_behind": lagging, "securities_measured": measured,
              "newest_close": common.isoformat(), "sessions_behind": common_behind,
              "last_session": last_session.isoformat()},
    ))
    return [result]


CheckFunc = Callable[[AsyncSession, WatchContext], Awaitable[list[CheckResult]]]
#: Order matters: leases first, because closing abandoned runs changes what "latest run" means.
CHECKS: tuple[CheckFunc, ...] = (
    check_leases,
    check_stale_sources,
    check_edgar_queue,
    check_quarantine,
    check_failed_runs,
    check_stale_prices,
)


# ---------------------------------------------------------------- bookkeeping
async def evaluate(session: AsyncSession, *, now: datetime, jobs: dict[str, Job], cursor: dict[str, Any],
                   stale_after: timedelta, checks: tuple[CheckFunc, ...] = CHECKS) -> Summary:
    """Run every check and reconcile what they see with the stored findings.

    * A condition seen now and not open: opened (after `confirm_after` runs in a row).
    * A condition seen now and already open: its numbers are refreshed, nothing is re-sent.
    * An open condition its check no longer sees: cleared.
    * An event: recorded, already closed.

    A check that raises is skipped (its open findings stay as they are) and
    named in the summary, so one broken check can't hide the others.
    """
    open_rows = {f.key: f for f in (await session.execute(
        select(WatchdogFinding).where(WatchdogFinding.cleared_at.is_(None)))).scalars()}
    wc = WatchContext(now=now, jobs=jobs, open_keys=set(open_rows), cursor=cursor, stale_after=stale_after)
    summary = Summary()
    results: list[CheckResult] = []
    for check in checks:
        try:
            async with session.begin_nested():  # a failing check rolls back only its own work
                results.extend(await check(session, wc))
        except Exception as exc:
            # The run's numbers are public (/status), so they name only the check; the error
            # itself goes to the worker's log.
            summary.check_errors.append(check.__name__)
            log.error("watchdog.check_failed", check=check.__name__, error=f"{type(exc).__name__}: {exc}"[:300])

    previously_seen: dict[str, int] = dict(cursor.get("seen") or {})
    seen_now: dict[str, int] = {}
    for result in results:
        observed: set[str] = set()
        for draft in result.drafts:
            if draft.kind == "event":
                session.add(_new_finding(draft, now, cleared=True))
                summary.events += 1
                continue
            observed.add(draft.key)
            row = open_rows.get(draft.key)
            if row is not None:
                row.last_seen_at, row.title, row.detail = now, draft.title, draft.detail
                row.data, row.severity, row.subject = draft.data, draft.severity, draft.subject
                summary.still_open += 1
                continue
            times_seen = previously_seen.get(draft.key, 0) + 1
            if times_seen < result.confirm_after:
                seen_now[draft.key] = times_seen  # remember, but don't report yet
                continue
            session.add(_new_finding(draft, now, cleared=False))
            summary.opened += 1
        for key, row in open_rows.items():
            if row.check_name == result.check and row.kind == "condition" and key not in observed:
                row.cleared_at = now
                row.clear_alert = clear_alert_for(row.open_alert)
                summary.cleared += 1
    cursor["seen"] = seen_now
    await session.flush()
    return summary


def _new_finding(draft: Draft, now: datetime, *, cleared: bool) -> WatchdogFinding:
    return WatchdogFinding(
        key=draft.key, check_name=draft.check, kind=draft.kind, severity=draft.severity,
        subject=draft.subject, title=draft.title, detail=draft.detail, data=draft.data,
        opened_at=now, last_seen_at=now, cleared_at=now if cleared else None,
        open_alert=initial_open_alert(draft.severity), clear_alert="none",
    )


# ---------------------------------------------------------------- the job
async def run_watchdog(ctx: JobContext) -> None:
    """The scheduled job (every 5 minutes): check, record, then alert."""
    from smt.worker.jobs import all_jobs  # local: smt.worker.jobs registers this module

    settings = get_settings()
    registry = {job.name: job for job in all_jobs()}
    async with session_scope() as session:
        now = (await session.execute(select(func.now()))).scalar_one()
        summary = await evaluate(session, now=now, jobs=registry, cursor=ctx.cursor,
                                 stale_after=timedelta(minutes=settings.watchdog_stale_minutes))
    status_url = settings.public_origin.rstrip("/") + "/status"
    sent = await dispatch_alerts(notifier_from_settings(), status_url=status_url)
    ctx.stats.update(summary.as_stats())
    ctx.stats["alerts"] = {"findings": sent.findings, "delivered": sent.delivered,
                           "channel": sent.channel, "error": plain_alert_error(sent.error)}
    if summary.opened or summary.cleared:
        log.info("watchdog.changes", **summary.as_stats())
