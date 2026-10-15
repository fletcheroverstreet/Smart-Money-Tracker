"""Operational status: per-job freshness, recent runs, quarantine, reconciliation, watchdog findings,
and how quickly new Form 4s reach the site.

Everything here is public, so error text is sent as plain sentences
(smt.worker.error_text): no Python class names, URLs or machine names. The
full errors stay in the run records and the worker's log.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from smt.alerts.notifier import split_urls
from smt.api.deps import Session
from smt.api.queries import entity_link, org_tickers
from smt.config import get_settings
from smt.db.models import (
    Filing,
    IngestionRun,
    Quarantine,
    ReconciliationResult,
    SchedulerJob,
    Transaction,
    WatchdogFinding,
)
from smt.domain.business_days import ET, is_sec_business_day, to_eastern
from smt.sources.edgar.coverage import checked_days
from smt.worker.error_text import looks_technical, plain_alert_error, plain_error, plain_reason
from smt.worker.schedules import EdgarHours

router = APIRouter(prefix="/api/status", tags=["status"])

#: How stale each job may get before /status shows it red (seconds since last OK run).
SLA_SECONDS: dict[str, int] = {
    "worker.heartbeat": 5 * 60,
    "people.refresh_stats": 20 * 60,
    "edgar.poll_ownership": 15 * 60,
    "edgar.poll_forms_3_5": 15 * 60,
    "edgar.poll_form144": 15 * 60,
    # The catch-up jobs that page back to the watermark (every 1-2 minutes in filing hours).
    "edgar.reach_back_form4": 15 * 60,
    "edgar.reach_back_other": 15 * 60,
    "edgar.process_queue": 15 * 60,
    "edgar.reconcile_daily_index": 26 * 3600,
    "edgar.retry_daily_index": 3 * 3600,       # hourly
    "edgar.refresh_public_float": 3 * 3600,    # every 30 minutes
    "edgar.expire_planned_sales": 26 * 3600,
    "edgar.fill_gaps": 3 * 3600,
    "edgar.load_new_dataset": 26 * 3600,
    "edgar.refresh_company_tickers": 26 * 3600,
    "edgar.refresh_submissions": 26 * 3600,
    "edgar.verify_names": 26 * 3600,
    "quality.flag_transactions": 90 * 60,
    "quality.repair_amendments": 26 * 3600,
    "worker.watchdog": 15 * 60,
    # NYSE trading days only: a Friday run followed by a Monday holiday is 96 hours apart.
    "prices.refresh_daily": 100 * 3600,
}
#: Resolved watchdog findings stay listed on /status for this long.
RECENT_FINDINGS = timedelta(days=7)
#: At most this many resolved findings are listed.
RECENT_FINDINGS_LIMIT = 50


#: A job that has never succeeded is "behind" only once its first run is this overdue.
NEVER_RUN_GRACE = timedelta(minutes=30)

#: The live goal (spec P1): a new Form 4 is on the site within 2 minutes of EDGAR accepting
#: it. It counts as met when 95% of filings in filing hours make it.
DETECTION_TARGET_SECONDS = 120
#: How far back the detection figures look.
DETECTION_WINDOW = timedelta(days=7)
#: The worker's heartbeat job runs every minute. A longer gap than this between two runs
#: means the worker was down (an outage), whatever the reason.
HEARTBEAT_GAP = timedelta(minutes=5)
#: EDGAR's filing hours, 06:00-22:00 ET on SEC business days: the window in which the
#: Form 4 feed is polled every 20 seconds. Outside it the polls run every 10 minutes, so
#: those filings would say nothing about the live goal.
FILING_HOURS = EdgarHours()
#: Shown instead of a finding's detail if it still reads like a log line. The watchdog writes
#: plain sentences, but findings stored by an older version stay listed for 7 days.
TECHNICAL_DETAIL_PUBLIC = "The technical details are in the worker's log."
#: Finding data that older versions stored and the public page must not carry: the lease
#: holder is "hostname:pid", the owner's machine name.
_PRIVATE_DATA_KEYS = frozenset({"leased_by"})


def _is_stale(sla: int | None, last_ok_at: datetime | None, next_run_at: datetime, now: datetime) -> bool:
    """Behind schedule: no success within the job's limit.

    A job that has never succeeded (a new job, or a nightly job on its first
    day) is behind only when its scheduled run is overdue, so a job waiting
    for its first 11 PM run isn't reported as late.
    """
    if not sla:
        return False
    if last_ok_at is not None:
        return (now - last_ok_at).total_seconds() > sla
    return now - next_run_at > NEVER_RUN_GRACE


class JobStatus(BaseModel):
    name: str
    enabled: bool
    last_ok_at: datetime | None
    next_run_at: datetime
    running: bool
    last_status: str | None
    last_error: str | None = Field(
        description="why the latest run failed, as one plain sentence (the full error is in the worker's log)")
    last_stats: dict[str, Any]
    sla_seconds: int | None
    stale: bool


class WatchdogFindingOut(BaseModel):
    """One problem the watchdog noticed (smt.worker.watchdog), and what was sent about it."""

    id: int
    key: str = Field(description="stable identity of the problem, e.g. stale_source:edgar.poll_ownership")
    check: str = Field(description="stale_source | edgar_queue | quarantine | lease | abandoned_runs | job_failed")
    kind: str = Field(description="condition (open until it stops holding) | event (recorded already resolved)")
    severity: str = Field(description="info | warning | critical")
    subject: str | None = Field(description="the job or source concerned, when there is one")
    title: str = Field(description="one plain sentence")
    detail: str
    data: dict[str, Any] = Field(description="the numbers behind the finding")
    opened_at: datetime
    last_seen_at: datetime = Field(description="the last watchdog run that still saw the problem")
    cleared_at: datetime | None = Field(description="null while the problem is still open")
    open_alert: str = Field(description="alert about the start: pending | sent | logged | failed | skipped | none")
    clear_alert: str = Field(description="alert about the end: pending | sent | logged | failed | skipped | none")
    alert_error: str | None = Field(description="why the last delivery attempt failed, if it did")


class WatchdogReport(BaseModel):
    """The watchdog's view: open problems and recently resolved ones."""

    last_run_at: datetime | None = Field(description="last successful watchdog run; null if it has never run")
    alert_channel: str = Field(description="apprise (APPRISE_URLS is set) | log (alerts only go to the log)")
    open: list[WatchdogFindingOut] = Field(description="problems still open, most severe first")
    recent: list[WatchdogFindingOut] = Field(description="problems resolved in the last 7 days, newest first")


class DetectionDay(BaseModel):
    """One business day of the live Form 4 figures (see DetectionDelay), in Eastern time."""

    day: date
    filings: int = Field(description="filings behind the median and 95th percentile that day")
    p50_seconds: float | None
    p95_seconds: float | None
    max_seconds: float | None = Field(description="the slowest of those filings")
    filings_during_outages: int
    found_later: int


class DetectionDelay(BaseModel):
    """How quickly new Form 4s reach the site: from EDGAR accepting a filing to it being loaded here.

    Only Forms 4 and 4/A accepted in filing hours count, so the slow off-hours polling
    doesn't blur the figure. Filings that were waiting while the worker was down (a gap of
    over 5 minutes in its heartbeat, at any hour) are counted separately and left out of the
    median and the 95th percentile, and so are filings the live feed missed and a later
    check found. `outages` counts only the stops that reach into filing hours, so a filing
    can be set apart for a stop that isn't in that count (accepted at 9:59 PM ET, say, with
    the worker stopped overnight).
    """

    forms: list[str] = Field(description="the forms measured: Form 4 and its amendment, 4/A")
    window_days: int = Field(description="how far back the figures look")
    since: datetime | None = Field(
        description="start of the measured period: 7 days ago, or the worker's first heartbeat if that is "
                    "later; null if the worker has never run")
    filings: int = Field(description="filings behind the median and 95th percentile")
    p50_seconds: float | None = Field(description="median seconds from acceptance to on the site")
    p95_seconds: float | None = Field(description="95% of those filings were on the site within this many seconds")
    target_seconds: int = Field(description="the live goal: 95% of filings within this many seconds")
    target_met: bool | None = Field(description="p95_seconds <= target_seconds; null when there's nothing to measure")
    outages: int = Field(description="times the worker was down (heartbeat gap over 5 minutes) during filing hours")
    outage_seconds: float = Field(description="how long those outages lasted within filing hours, in all")
    filings_during_outages: int = Field(
        description="filings accepted while the worker was down, or within the target time (2 minutes) "
                    "before it stopped, so the live feed never had a fair chance at them; not in the figures")
    found_later: int = Field(
        description="filings the live feed missed although the worker was running, found later by the daily "
                    "index checks or the gap filler; included in the figures with their full wait")
    days: list[DetectionDay] = Field(
        default_factory=list,
        description="the same figures for each business day in the window, newest first, so a change to the "
                    "collector shows from the day it ran")


class StatusReport(BaseModel):
    generated_at: datetime
    jobs: list[JobStatus]
    counts: dict[str, int]
    quarantine_open: int
    reconciliation: list[dict[str, Any]]
    detection: DetectionDelay
    watchdog: WatchdogReport


def _public_data(data: dict[str, Any]) -> dict[str, Any]:
    """A finding's numbers as the public page may carry them. Findings written before the
    watchdog's text was made plain can hold the lease holder's machine name and raw errors."""
    out = {k: v for k, v in data.items() if k not in _PRIVATE_DATA_KEYS}
    if isinstance(out.get("last_error"), str):
        out["last_error"] = plain_error(out["last_error"])
    examples = out.get("examples")
    if isinstance(examples, list):
        cleaned: list[Any] = []
        for example in examples:
            if isinstance(example, dict):
                example = {k: v for k, v in example.items() if k != "error"}  # a raw queue error
                if isinstance(example.get("reason"), str):
                    example["reason"] = plain_reason(example["reason"])
            cleaned.append(example)
        out["examples"] = cleaned
    return out


def _finding_out(f: WatchdogFinding) -> WatchdogFindingOut:
    return WatchdogFindingOut(
        id=f.id, key=f.key, check=f.check_name, kind=f.kind, severity=f.severity, subject=f.subject,
        title=f.title, detail=TECHNICAL_DETAIL_PUBLIC if looks_technical(f.detail) else f.detail,
        data=_public_data(f.data or {}), opened_at=f.opened_at, last_seen_at=f.last_seen_at,
        cleared_at=f.cleared_at, open_alert=f.open_alert, clear_alert=f.clear_alert,
        # smt.alerts.dispatch stores the notification library's exception text as is.
        alert_error=plain_alert_error(f.alert_error))


async def watchdog_report(session: AsyncSession, now: datetime,
                          jobs: list[SchedulerJob]) -> WatchdogReport:
    """Open findings (most severe, then newest first) and those resolved in the last week."""
    rows = (await session.execute(
        select(WatchdogFinding)
        .where(or_(WatchdogFinding.cleared_at.is_(None), WatchdogFinding.cleared_at > now - RECENT_FINDINGS))
        .order_by(WatchdogFinding.opened_at.desc(), WatchdogFinding.id.desc())
    )).scalars().all()
    rank = {"critical": 0, "warning": 1, "info": 2}
    open_rows = sorted((r for r in rows if r.cleared_at is None), key=lambda r: rank.get(r.severity, 3))
    recent = sorted((r for r in rows if r.cleared_at is not None),
                    key=lambda r: (r.cleared_at or r.opened_at, r.id), reverse=True)[:RECENT_FINDINGS_LIMIT]
    last_run = next((j.last_ok_at for j in jobs if j.name == "worker.watchdog"), None)
    channel = "apprise" if split_urls(get_settings().apprise_urls.get_secret_value()) else "log"
    return WatchdogReport(last_run_at=last_run, alert_channel=channel,
                          open=[_finding_out(r) for r in open_rows],
                          recent=[_finding_out(r) for r in recent])


# ---------------------------------------------------------------- detection delay
def filing_hours_overlap(start: datetime, end: datetime) -> timedelta:
    """How much of the span start..end falls in EDGAR filing hours (06:00-22:00 ET, SEC business days)."""
    total = timedelta(0)
    day, last = to_eastern(start).date(), to_eastern(end).date()
    while day <= last:
        if is_sec_business_day(day):
            opens = datetime.combine(day, FILING_HOURS.opens, tzinfo=ET)
            closes = datetime.combine(day, FILING_HOURS.closes, tzinfo=ET)
            lo, hi = max(start, opens), min(end, closes)
            if hi > lo:
                total += hi - lo
        day += timedelta(days=1)
    return total


async def worker_outages(session: AsyncSession, since: datetime, now: datetime) -> list[tuple[datetime, datetime]]:
    """Every span when the worker was down, from gaps in its heartbeat, at any hour.

    The heartbeat job (smt.worker.jobs.heartbeat) records a run in ingestion_runs every
    minute. Two runs more than HEARTBEAT_GAP apart mean the worker was down from the first
    to the second. If the latest run is already too old, the worker is down now: that span
    ends at `now`. detection_delay counts as outages only the spans that reach into filing
    hours, but uses all of them to set filings apart: a Form 4 accepted at 9:59 PM ET that
    waited through a night-time stop was waiting on the worker, not on the live check.
    """
    rows = (await session.execute(text(
        "WITH beats AS ("
        "  SELECT started_at FROM ingestion_runs WHERE job = 'worker.heartbeat' AND started_at <= :now"
        "  AND started_at >= coalesce((SELECT max(started_at) FROM ingestion_runs"
        "                              WHERE job = 'worker.heartbeat' AND started_at < :since), :since)) "
        "SELECT down_from, down_to FROM ("
        "  SELECT lag(started_at) OVER (ORDER BY started_at) AS down_from, started_at AS down_to FROM beats) g "
        "WHERE down_to - down_from > :gap "
        "UNION ALL "
        "SELECT max(started_at), CAST(:now AS timestamptz) FROM beats HAVING :now - max(started_at) > :gap "
        "ORDER BY 1"),
        {"since": since, "now": now, "gap": HEARTBEAT_GAP})).all()
    return [(down_from, down_to) for down_from, down_to in rows]


#: The worker's stops, from worker_outages, as a table the SQL below can join (bind
#: :down_from and :down_to as lists of the same length).
OUTAGES_CTE = """
        WITH outages AS (
            SELECT * FROM unnest(CAST(:down_from AS timestamptz[]), CAST(:down_to AS timestamptz[]))
                AS o(down_from, down_to)
        )"""
#: True when the worker was down within the target time (bind :target, seconds) after EDGAR
#: accepted filing `f`, so the live feed had no fair chance at it. A filing the feed simply
#: missed, or was slow on, while the worker ran is the collector's own miss. The detection
#: figures and the reconciliation table both use this one rule, so they can't disagree.
IN_OUTAGE = """EXISTS (SELECT 1 FROM outages o
                           WHERE o.down_from < f.accepted_at + make_interval(secs => :target)
                             AND o.down_to > f.accepted_at)"""


async def detection_delay(session: AsyncSession, now: datetime) -> DetectionDelay:
    """The live Form 4 figures for the Status page (see DetectionDelay)."""
    first_beat: datetime | None = (await session.execute(text(
        "SELECT min(started_at) FROM ingestion_runs WHERE job = 'worker.heartbeat'"))).scalar_one()
    since = max(now - DETECTION_WINDOW, first_beat) if first_beat else None
    result = DetectionDelay(
        forms=["4", "4/A"], window_days=DETECTION_WINDOW.days, since=since, filings=0, p50_seconds=None,
        p95_seconds=None, target_seconds=DETECTION_TARGET_SECONDS, target_met=None, outages=0,
        outage_seconds=0.0, filings_during_outages=0, found_later=0)
    if since is None:
        return result  # the worker has never run, so nothing was detected live

    down = await worker_outages(session, since, now)
    # The outages the page reports are the stops that reach into filing hours. Every stop,
    # at any hour, still sets apart the filings that waited through it (the SQL below).
    overlaps = [filing_hours_overlap(max(a, since), min(b, now)) for a, b in down]
    in_hours = [overlap for overlap in overlaps if overlap > timedelta(0)]
    result.outages = len(in_hours)
    result.outage_seconds = sum(in_hours, timedelta(0)).total_seconds()

    # SEC business days in the window, in Eastern time (weekends and federal holidays excluded).
    first_day, last_day = to_eastern(since).date(), to_eastern(now).date()
    open_days: list[date] = [first_day + timedelta(days=n) for n in range((last_day - first_day).days + 1)
                             if is_sec_business_day(first_day + timedelta(days=n))]
    measured = OUTAGES_CTE + """, measured AS (
            SELECT f.detected_via = 'feed' AS live,
                   (f.accepted_at AT TIME ZONE 'America/New_York')::date AS day,
                   extract(epoch FROM f.detected_at - f.accepted_at) AS seconds,
                   """ + IN_OUTAGE + """ AS in_outage
            FROM filings f
            WHERE f.source = 'edgar' AND f.form_type IN ('4', '4/A')
              AND f.accepted_at >= :since AND f.accepted_at <= :now
              AND (f.accepted_at AT TIME ZONE 'America/New_York')::date = ANY(CAST(:open_days AS date[]))
              AND (f.accepted_at AT TIME ZONE 'America/New_York')::time >= :opens
              AND (f.accepted_at AT TIME ZONE 'America/New_York')::time < :closes
        )
        SELECT {group} count(*) FILTER (WHERE NOT in_outage) AS filings,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY seconds) FILTER (WHERE NOT in_outage) AS p50,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY seconds) FILTER (WHERE NOT in_outage) AS p95,
               count(*) FILTER (WHERE in_outage) AS during_outages,
               count(*) FILTER (WHERE NOT live AND NOT in_outage) AS found_later,
               max(seconds) FILTER (WHERE NOT in_outage) AS slowest
        FROM measured {by}"""
    params = {"down_from": [a for a, _ in down], "down_to": [b for _, b in down], "since": since, "now": now,
              "open_days": open_days, "opens": FILING_HOURS.opens, "closes": FILING_HOURS.closes,
              "target": DETECTION_TARGET_SECONDS}
    row = (await session.execute(text(measured.format(group="", by="")), params)).one()
    result.days = [DetectionDay(
        day=r.day, filings=int(r.filings), p50_seconds=float(r.p50) if r.p50 is not None else None,
        p95_seconds=float(r.p95) if r.p95 is not None else None,
        max_seconds=float(r.slowest) if r.slowest is not None else None,
        filings_during_outages=int(r.during_outages), found_later=int(r.found_later),
    ) for r in (await session.execute(text(measured.format(group="day,", by="GROUP BY day ORDER BY day DESC")),
                                      params)).all()]
    result.filings, result.filings_during_outages, result.found_later = int(row[0]), int(row[3]), int(row[4])
    result.p50_seconds = float(row[1]) if row[1] is not None else None
    result.p95_seconds = float(row[2]) if row[2] is not None else None
    if result.p95_seconds is not None:
        result.target_met = result.p95_seconds <= DETECTION_TARGET_SECONDS
    return result


# ---------------------------------------------------------------- what the nightly check found
@dataclass(frozen=True)
class ReconciliationFinds:
    """Filings a day's check against the SEC daily index found that the live feed hadn't.

    `missed_live`: EDGAR accepted them while the worker was collecting, so the live feed
    should have had them. These are the collector's real misses; the goal is zero.
    `while_down`: accepted while the worker was stopped, or within the target time before it
    stopped (the same rule as the detection figures), so the live feed never had a chance.
    Both are None on a day the worker wasn't collecting at any point in filing hours
    ("Loaded afterwards").
    """

    missed_live: int | None
    while_down: int | None


#: Before the worker's first heartbeat nothing was collected live. That time is treated as one
#: long stop starting here (any date before the site's first filing would do).
_BEFORE_LIVE = datetime(2000, 1, 1, tzinfo=UTC)
#: A day's index can list filings accepted the evening before (a Form 144 accepted after
#: 5:30 PM ET is dated the next business day), so the worker's stops are read from this much
#: earlier than the oldest day shown.
_OUTAGE_LOOKBACK = timedelta(days=7)


def _merged(spans: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    """Overlapping or touching spans joined into one, in time order."""
    out: list[tuple[datetime, datetime]] = []
    for start, end in sorted(spans):
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def collected_in_filing_hours(day: date, down: list[tuple[datetime, datetime]], now: datetime) -> bool:
    """Whether the worker was running at any moment of `day`'s filing hours (06:00-22:00 ET) up
    to `now`, given the spans it was down (merged, as _merged returns them)."""
    opens = datetime.combine(day, FILING_HOURS.opens, tzinfo=ET)
    closes = min(datetime.combine(day, FILING_HOURS.closes, tzinfo=ET), now)
    if closes <= opens:
        return False
    stopped = sum((min(b, closes) - max(a, opens) for a, b in down if min(b, closes) > max(a, opens)),
                  timedelta(0))
    return stopped < closes - opens


async def reconciliation_finds(session: AsyncSession, days: list[date],
                               now: datetime) -> dict[date, ReconciliationFinds]:
    """For each checked day, the filings the daily-index checks found, split filing by filing by
    whether the worker was collecting when EDGAR accepted each one (see ReconciliationFinds).

    Round 6 (2026-09-28) showed why this is decided per filing, not per day. The old rule
    called a day live whenever the feed had queued anything for it, and charged every find to
    the live feed: Sep 21-23 read 448, 413 and 62 "found by the check", yet the worker first
    ran on Sep 24 at 8:04 AM ET (the feed rows were its first poll paging back), and all 148
    finds for Sep 28 were accepted between 4:45 and 10 PM ET while the host was asleep. None
    was a filing missed while the feed was running. The opposite error happened too: one gap
    filler row made a live day read "Loaded afterwards", hiding its real misses.

    Finds come from the queue (a later re-check of the day doesn't erase them): the nightly
    check's and the gap filler's, which reads the same index. A find is placed once it has
    loaded, since its acceptance time comes from the filing itself; until then it is in the
    day's "still missing" count.
    """
    loaded_afterwards = {d: ReconciliationFinds(None, None) for d in days}
    if not days:
        return loaded_afterwards
    first_beat: datetime | None = (await session.execute(text(
        "SELECT min(started_at) FROM ingestion_runs WHERE job = 'worker.heartbeat'"))).scalar_one()
    if first_beat is None:
        return loaded_afterwards  # the worker has never run
    since = max(first_beat, datetime.combine(min(days), time(0), tzinfo=ET) - _OUTAGE_LOOKBACK)
    down = _merged([(_BEFORE_LIVE, first_beat), *await worker_outages(session, since, now)])
    live_days = [d for d in days if collected_in_filing_hours(d, down, now)]
    if not live_days:
        return loaded_afterwards
    rows = (await session.execute(text(OUTAGES_CTE + """
        SELECT x.day, count(*) FILTER (WHERE NOT x.in_outage) AS missed_live,
               count(*) FILTER (WHERE x.in_outage) AS while_down
        FROM (SELECT q.filed_on AS day, """ + IN_OUTAGE + """ AS in_outage
              FROM edgar_queue q JOIN filings f ON f.source = 'edgar' AND f.source_key = q.accession
              WHERE q.filed_on = ANY(:days) AND q.discovered_via IN ('daily_index', 'gap_fill')
                AND f.accepted_at IS NOT NULL) x
        GROUP BY x.day"""), {"down_from": [a for a, _ in down], "down_to": [b for _, b in down],
                             "days": live_days, "target": DETECTION_TARGET_SECONDS})).all()
    found = {r.day: r for r in rows}
    out = dict(loaded_afterwards)
    for d in live_days:
        r = found.get(d)
        out[d] = ReconciliationFinds(int(r.missed_live) if r else 0, int(r.while_down) if r else 0)
    return out


@router.get("")
async def status(session: Session) -> StatusReport:
    """Everything the Status page shows, in one call."""
    now = (await session.execute(select(func.now()))).scalar_one()
    jobs = (await session.execute(select(SchedulerJob).order_by(SchedulerJob.name))).scalars().all()
    last_runs: dict[int, IngestionRun] = {}
    run_ids = [j.last_run_id for j in jobs if j.last_run_id]
    if run_ids:
        for found in (await session.execute(select(IngestionRun).where(IngestionRun.id.in_(run_ids)))).scalars():
            last_runs[found.id] = found

    out: list[JobStatus] = []
    for job in jobs:
        run = last_runs.get(job.last_run_id or -1)
        sla = SLA_SECONDS.get(job.name)
        stale = _is_stale(sla, job.last_ok_at, job.next_run_at, now)
        out.append(JobStatus(
            name=job.name, enabled=job.enabled, last_ok_at=job.last_ok_at, next_run_at=job.next_run_at,
            running=bool(job.lease_until and job.lease_until > now),
            last_status=run.status if run else None, last_error=plain_error(run.error) if run and run.error else None,
            last_stats=run.stats if run else {}, sla_seconds=sla, stale=stale,
        ))

    counts = {
        "filings": (await session.execute(select(func.count()).select_from(Filing))).scalar_one(),
        "transactions": (await session.execute(select(func.count()).select_from(Transaction))).scalar_one(),
    }
    quarantine_open = (await session.execute(
        select(func.count()).select_from(Quarantine).where(Quarantine.resolved_at.is_(None))
    )).scalar_one()
    recon = (await session.execute(
        select(ReconciliationResult).order_by(ReconciliationResult.day.desc()).limit(10)
    )).scalars().all()
    held_now = await checked_days(session)
    finds = await reconciliation_finds(session, [r.day for r in recon], now)
    return StatusReport(
        generated_at=now, jobs=out, counts=counts, quarantine_open=quarantine_open,
        # found and missing are counted now, not at the time of the check: missing filings are
        # queued and load afterwards. missing_at_check (filings the live feed missed while the
        # worker ran) and found_while_down (filings the check found that were accepted while it
        # was stopped; not every filing accepted then, since the feed's catch-up loads most of
        # those after a restart) are None on a day the worker wasn't collecting at all: see
        # reconciliation_finds.
        reconciliation=[{"source": r.source, "day": r.day.isoformat(), "expected": r.expected,
                         "missing_at_check": finds[r.day].missed_live,
                         "found_while_down": finds[r.day].while_down,
                         "found": held_now.get(r.day, (r.expected, r.found))[1],
                         "missing": r.expected - held_now.get(r.day, (r.expected, r.found))[1]} for r in recon],
        detection=await detection_delay(session, now),
        watchdog=await watchdog_report(session, now, list(jobs)),
    )


class CoverageRow(BaseModel):
    source: str
    parse_status: str
    documents: int


class QueueRow(BaseModel):
    status: str
    items: int
    oldest: datetime | None


class QuarantineRow(BaseModel):
    id: int
    source: str
    filing_ref: str | None
    #: The filing on EDGAR (its index page if loaded, else the document that failed to parse).
    filing_url: str | None = None
    reason: str
    created_at: datetime


class Coverage(BaseModel):
    documents: list[CoverageRow]
    queue: list[QueueRow]
    quarantine: list[QuarantineRow]


@router.get("/coverage")
async def coverage(session: Session) -> Coverage:
    """Found vs parsed vs quarantined, per source; the fetch queue; open quarantine items."""
    docs = (await session.execute(text(
        "SELECT source, parse_status, count(*) FROM raw_documents WHERE doc_role = 'primary' "
        "GROUP BY source, parse_status ORDER BY source, parse_status"))).all()
    queue = (await session.execute(text(
        "SELECT status, count(*), min(discovered_at) FROM edgar_queue GROUP BY status ORDER BY status"))).all()
    quarantined = (await session.execute(text(
        "SELECT q.id, q.source, q.filing_ref, q.reason, q.created_at, coalesce(f.url, r.url) AS url "
        "FROM quarantine q LEFT JOIN filings f ON f.source = q.source AND f.source_key = q.filing_ref "
        "LEFT JOIN raw_documents r ON r.id = q.raw_document_id "
        "WHERE q.resolved_at IS NULL ORDER BY q.created_at DESC LIMIT 50"))).all()
    return Coverage(
        documents=[CoverageRow(source=s, parse_status=p, documents=n) for s, p, n in docs],
        queue=[QueueRow(status=s, items=n, oldest=o) for s, n, o in queue],
        quarantine=[QuarantineRow(id=q.id, source=q.source, filing_ref=q.filing_ref, filing_url=q.url,
                                  reason=q.reason, created_at=q.created_at) for q in quarantined],
    )


class SpotCheckRow(BaseModel):
    #: "form345" (a trade from Forms 3/4/5) or "form144" (a notice of a planned sale).
    source: str = "form345"
    transaction_id: int | None
    accession: str
    form_type: str
    #: The filer's name as the site shows it (a company's in its own capitals, not EDGAR's).
    owner: str
    #: The filer's profile: /people/{owner_slug}.
    owner_slug: str | None = None
    #: The company's name as the site shows it.
    issuer: str | None
    #: The company's ticker, for a link to its ticker page (none when it has no ticker).
    issuer_ticker: str | None = None
    code: str | None
    traded_on: Any
    shares: Any
    price: Any
    filing_url: str
    document_url: str | None
    #: A trade's dollar value, or a Form 144 notice's estimated market value as the filer stated it.
    value: Any = None
    #: A trade's direction: "buy", "sell", "exchange" or "other" (none for a Form 144 notice).
    direction: str | None = None
    #: A trade's data-quality flags (smt.ingest.quality), so a figure that can't be right as
    #: filed reads "Check filing" here as it does in every table.
    data_issues: list[str] = []


@router.get("/spot-check")
async def spot_check(session: Session, limit: int = 20, days: int = 30) -> list[SpotCheckRow]:
    """Random recent rows from each source beside links to their original documents, for checking
    by eye (spec §11): `limit` trades from Forms 3/4/5 and `limit` Form 144 notices, from the last
    30 days, taken only from filings loaded from their own XML (so the document link is there)."""
    n = min(max(limit, 1), 100)
    window = min(max(days, 1), 3650)
    # Names read as on every other page (smt.api.queries.entity_link): a company's EDGAR
    # capitals ("DOCUSIGN, INC.") become "Docusign, Inc.", keeping its own tickers in capitals.
    # The company links to its ticker page: the security's ticker, else any of the issuer's.
    people = ("o.name AS owner, o.slug AS owner_slug, o.kind AS owner_kind, i.name AS issuer, "
              f"i.kind AS issuer_kind, i.slug AS issuer_slug, {org_tickers('i')} AS issuer_tickers, "
              "coalesce(s.primary_ticker, (SELECT min(x.primary_ticker) FROM securities x "
              "                            WHERE x.issuer_entity_id = i.id)) AS issuer_ticker")
    trades = (await session.execute(text(f"""
        SELECT t.id, f.source_key, f.form_type, {people}, t.code, t.traded_on,
               t.shares, t.price, t.value, t.direction, t.data_issues, f.url, f.primary_doc_url
        FROM transactions t
        JOIN filings f ON f.id = t.filing_id JOIN entities o ON o.id = t.entity_id
        LEFT JOIN entities i ON i.id = t.issuer_entity_id
        LEFT JOIN securities s ON s.id = t.security_id
        WHERE f.filed_on >= current_date - :days AND f.primary_doc_url IS NOT NULL
          AND f.parser_version NOT LIKE 'dataset/%'
        ORDER BY random() LIMIT :n"""), {"n": n, "days": window})).all()
    notices = (await session.execute(text(f"""
        SELECT f.source_key, f.form_type, {people}, p.approx_sale_on, p.shares,
               p.market_value, f.url, f.primary_doc_url
        FROM planned_sales p
        JOIN filings f ON f.id = p.filing_id JOIN entities o ON o.id = p.seller_entity_id
        LEFT JOIN entities i ON i.id = p.issuer_entity_id
        LEFT JOIN securities s ON s.id = p.security_id
        WHERE f.filed_on >= current_date - :days AND f.superseded_by_id IS NULL
        ORDER BY random() LIMIT :n"""), {"n": n, "days": window})).all()

    def names(r: Any) -> dict[str, Any]:
        """The owner and the company as the page shows and links them."""
        issuer = (entity_link(r.issuer, r.issuer_slug, r.issuer_kind or "company", r.issuer_tickers or ()).name
                  if r.issuer else None)
        return {"owner": entity_link(r.owner, r.owner_slug, r.owner_kind).name, "owner_slug": r.owner_slug,
                "issuer": issuer, "issuer_ticker": r.issuer_ticker if r.issuer else None}

    return [SpotCheckRow(source="form345", transaction_id=r.id, accession=r.source_key, form_type=r.form_type,
                         **names(r), code=r.code, traded_on=r.traded_on, shares=r.shares,
                         price=r.price, value=r.value, direction=r.direction, data_issues=list(r.data_issues or []),
                         filing_url=r.url, document_url=r.primary_doc_url) for r in trades] + [
        SpotCheckRow(source="form144", transaction_id=None, accession=r.source_key, form_type=r.form_type,
                     **names(r), code=None, traded_on=r.approx_sale_on, shares=r.shares,
                     price=None, value=r.market_value, filing_url=r.url, document_url=r.primary_doc_url)
        for r in notices]
