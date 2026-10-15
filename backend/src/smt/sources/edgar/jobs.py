"""EDGAR jobs run by the worker's scheduler (registered in smt.worker.jobs).

    poll_ownership          every 20 s in filing hours: the newest page of the Form 4 feed
    poll_forms_3_5          every 60 s in filing hours: the newest page for Forms 3 and 5
    poll_form144            every 60 s in filing hours: the newest page for Form 144
    reach_back_form4        every 60 s in filing hours: Form 4 feed pages back to the watermark
    reach_back_other        every 120 s in filing hours: the same for Forms 3, 5 and 144
    process_queue           every 15 s: fetch, store, parse and load queued filings
    reconcile_daily_index   23:00 ET: compare against the official daily index
    fill_gaps               every 10 minutes: queue filings for dates not held yet (daily indexes)
    load_new_dataset        daily: load a newly published quarterly data set
    expire_planned_sales    daily: Form 144 notices with no Form 4 after 90 days

(The security master's jobs live in reference.py and submissions.py.)

Live collection has two parts (critic round 6, the P1 two-minute goal):

* The polls read only the newest page (page 0) of a feed, on a fixed cadence
  that does not wait for earlier reads: up to 3 reads of a feed can be in
  flight at once (Job.max_in_flight), because at peak EDGAR takes 20-60 s to
  build a page and, with reads in series, one slow page held up every filing
  accepted meanwhile. Each read queues the filings it finds that are new and
  processes them straight away, newest first. The queue's unique accession
  and its claim (FOR UPDATE SKIP LOCKED) make sure a filing two reads both
  see is queued once and processed once.
* The reach-back jobs page further back, to the newest filing the last
  completed reach-back saw (its watermark) minus WATERMARK_SLACK, until a
  whole page is known. Only they move the watermark. This covers what page 0
  can't: a burst larger than one page, and reads that failed or were cut
  short. The polls used to do this paging themselves, so a busy poll spent
  time re-reading pages of filings it already held.

Anything left over or failed is picked up by process_queue.
"""

from __future__ import annotations

import time as clock
from datetime import date, datetime, time, timedelta

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from smt.db.models import EdgarQueue, Filing, ReconciliationResult
from smt.db.session import session_scope
from smt.domain.business_days import ET, is_sec_business_day
from smt.ingest.form144 import expire_unmatched, repair_notice_versions
from smt.logs import get_logger
from smt.net.client import FEED_HEDGE_AFTER, FEED_TIMEOUT, FetchError, HttpClient, shared_client
from smt.net.ratelimit import Priority
from smt.net.rawstore import RawStore
from smt.sources.edgar.atom import FeedFiling, group_filings, parse_feed
from smt.sources.edgar.coverage import SOURCE as COVERAGE_SOURCE
from smt.sources.edgar.coverage import day_coverage
from smt.sources.edgar.daily_index import daily_index_url, parse_master_index
from smt.sources.edgar.pipeline import (
    FORM144_FORMS,
    OWNERSHIP_FORMS,
    SUPPORTED_FORMS,
    enqueue,
)
from smt.sources.edgar.pipeline import (
    process_queue as run_queue,
)
from smt.worker.error_text import fetch_error_status
from smt.worker.scheduler import JobContext

log = get_logger("edgar.jobs")

FEED = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type={type}&owner={owner}&count=100&start={start}&output=atom"
MAX_PAGES = 25
#: How far past the last completed poll's newest filing a poll pages back (clock and order slack).
WATERMARK_SLACK = timedelta(minutes=10)


#: Attempts for one read of a feed's newest page. Fewer than the client's default, because a
#: fresh read starts every 20 s anyway: a read stuck retrying for minutes would only hold one of
#: the few in-flight places, and anything it would have found is found by the next read or by
#: the reach-back job.
NEWEST_PAGE_ATTEMPTS = 2

FORM4_FEEDS: list[tuple[str, str]] = [("4", "only")]
FORMS_3_5_FEEDS: list[tuple[str, str]] = [("3", "only"), ("5", "only")]
FORM144_FEEDS: list[tuple[str, str]] = [("144", "include")]


async def _read_page(ctx: JobContext, client: HttpClient, type_: str, owner: str, page: int, forms: frozenset[str],
                     *, attempts: int | None = None) -> tuple[int, list[FeedFiling]]:
    """Fetch one feed page. Returns (number of entries, the page's filings of `forms`)."""
    started = clock.monotonic()
    fetched = await client.get(FEED.format(type=type_, owner=owner, start=page * 100),
                               priority=Priority.LIVE, hedge_after=FEED_HEDGE_AFTER, timeouts=FEED_TIMEOUT,
                               attempts=attempts)
    # Where a slow run spends its time: the feed requests (with any retries), or the rest.
    took = clock.monotonic() - started
    ctx.stats["feed_seconds"] = round(ctx.stats.get("feed_seconds", 0.0) + took, 2)
    ctx.stats["slowest_page_seconds"] = round(max(ctx.stats.get("slowest_page_seconds", 0.0), took), 2)
    ctx.docs_fetched += 1
    entries = parse_feed(fetched.content)
    return len(entries), group_filings(entries, forms)


async def _queue_new(filings: list[FeedFiling]) -> tuple[set[str], list[FeedFiling]]:
    """Queue the filings not yet queued or loaded. Returns (the accessions known before, the
    filings this call queued).

    Two reads in flight at once often see the same new filing. The queue's unique accession
    decides which one queues it (INSERT ... ON CONFLICT DO NOTHING RETURNING), and only that
    one processes it. Filings are inserted in accession order, here and in the daily-index
    check (_reconcile_day), so two writers inserting the same rows take their row locks in the
    same order and can't deadlock."""
    known = await _known_accessions({f.accession for f in filings})
    added: list[FeedFiling] = []
    async with session_scope() as session:
        for f in sorted(filings, key=lambda f: f.accession):
            if f.accession not in known and await _enqueue_feed_filing(session, f):
                added.append(f)
    return known, added


async def _process_found(ctx: JobContext, client: HttpClient, found: list[FeedFiling], inline_limit: int) -> None:
    """Process what THIS run queued straight away, newest first and only a small batch.

    New filings reach Live without waiting for process_queue. Other queued work (gap filling,
    retries) is left to process_queue: taking it here once made every Form 4 poll spend minutes
    on backfill items and SEC retries, and new filings waited 10-28 minutes."""
    ctx.stats["discovered"] = ctx.stats.get("discovered", 0) + len(found)
    if not found:
        return
    newest_first = [f.accession for f in sorted(found, key=lambda f: f.accepted_at, reverse=True)]
    counts = await run_queue(client, RawStore(), limit=inline_limit, priority=Priority.LIVE,
                             accessions=newest_first[:inline_limit])
    ctx.rows_new += counts["done"]
    ctx.parse_failures += counts["failed"]
    ctx.stats.update({f"queue_{k}": v for k, v in counts.items()})


async def _read_newest(ctx: JobContext, feed_types: list[tuple[str, str]], forms: frozenset[str], *,
                       inline_limit: int = 100) -> None:
    """Read the newest page of each feed once, and queue and process what is new on it.

    Runs of this overlap (see the module docstring), so it keeps no state of its own: the queue
    is the record of what has been seen, and it never pages back or moves a watermark."""
    client = shared_client()
    found: list[FeedFiling] = []
    for type_, owner in feed_types:
        _count, filings = await _read_page(ctx, client, type_, owner, 0, forms, attempts=NEWEST_PAGE_ATTEMPTS)
        found += (await _queue_new(filings))[1]
    await _process_found(ctx, client, found, inline_limit)


async def _reach_back(ctx: JobContext, feed_types: list[tuple[str, str]], forms: frozenset[str], *,
                      inline_limit: int = 100, legacy_jobs: tuple[str, ...] = ()) -> None:
    """Page through a feed (newest first) until nothing new can be behind the current page.

    Stopping at the first page with any known filing left permanent holes: when paging was
    cut short (a restart, an SEC error, a run cancelled for outliving its lease), filings older
    than the next known one were never fetched live (13 Form 4s on 2026-09-24, found 30 hours
    later by the daily index). So paging goes on while a page still has unknown filings, and
    also until it reaches back past the newest filing the last COMPLETED run saw (its
    watermark, kept in the job's cursor, minus WATERMARK_SLACK). A run that is cut short (an
    error or a cancellation) never moves the watermark, so the next one covers what it missed.

    At most MAX_PAGES pages. A run that reads them all without reaching the watermark (after a
    long gap: the machine asleep, say, as on 2026-09-29 when one run overran by 5.6 hours)
    still moves the watermark to page 0's newest filing, and logs `reach_back.pages_exhausted`
    with how far back it got. The filings behind page MAX_PAGES are left to the daily-index
    reconciliation (reconcile_daily_index, which also runs when the machine wakes if 23:00 ET
    passed meanwhile). Keeping the old watermark instead would not bring them back: they only
    sink deeper in the feed, so every later run would read all MAX_PAGES pages again, once a
    minute, without ever reaching it.

    The polls kept these watermarks before the reach-back jobs existed. A feed with no
    watermark here yet starts from the one its poll left (`legacy_jobs`), so the switch leaves
    no hole.
    """
    client = shared_client()
    found: list[FeedFiling] = []
    marks: dict[str, str] = ctx.cursor.setdefault("watermarks", {})
    if legacy_jobs and any(type_ not in marks for type_, _owner in feed_types):
        for type_, legacy in (await _legacy_watermarks(legacy_jobs)).items():
            marks.setdefault(type_, legacy)
    for type_, owner in feed_types:
        mark = marks.get(type_)
        reach_back = datetime.fromisoformat(mark) - WATERMARK_SLACK if mark else None
        newest: datetime | None = None
        oldest_seen: datetime | None = None
        for page in range(MAX_PAGES):
            count, filings = await _read_page(ctx, client, type_, owner, page, forms)
            if not count:
                break  # "No recent filings": the end of the feed window
            if page == 0 and filings:
                newest = max(f.accepted_at for f in filings)
            known, added = await _queue_new(filings)
            found += added
            oldest = min((f.accepted_at for f in filings), default=None)
            if oldest is not None:
                oldest_seen = oldest if oldest_seen is None else min(oldest_seen, oldest)
            reached = reach_back is None or (oldest is not None and oldest <= reach_back)
            if count < 100 or (reached and all(f.accession in known for f in filings)):
                break
            ctx.stats["pages_back"] = ctx.stats.get("pages_back", 0) + 1
        else:
            # Every page was read and paging never ended: usually the watermark is still further
            # back (also a feed with no watermark yet, or a last page that still held new
            # filings). Anything behind `oldest_seen` wasn't read live (see the docstring for why
            # the watermark moves anyway). The run's stats and the log say so.
            ctx.stats.setdefault("pages_exhausted", []).append(type_)
            log.warning("reach_back.pages_exhausted", feed=type_, pages=MAX_PAGES, watermark=mark,
                        reached_back_to=oldest_seen.isoformat() if oldest_seen else None)
        # Reached only when paging ended (an error or a cancellation skips it).
        if newest is not None:
            marks[type_] = newest.isoformat()
    await _process_found(ctx, client, found, inline_limit)


async def _legacy_watermarks(jobs: tuple[str, ...]) -> dict[str, str]:
    """The watermarks the polls kept in their cursors before the reach-back jobs took over."""
    async with session_scope() as session:
        cursors = (await session.execute(text(
            "SELECT cursor FROM scheduler_jobs WHERE name = ANY(:names)"), {"names": list(jobs)})).scalars().all()
    marks: dict[str, str] = {}
    for cursor in cursors:
        for type_, mark in ((cursor or {}).get("watermarks") or {}).items():
            if isinstance(mark, str):
                marks[type_] = mark
    return marks


async def _enqueue_feed_filing(session: AsyncSession, f: FeedFiling) -> bool:
    return await enqueue(session, accession=f.accession, form_type=f.form_type, cik=f.any_cik,
                         accepted_at=f.accepted_at, filed_on=f.filed_on, discovered_via="feed",
                         filer_names={cik: name for cik, name, _role in f.parties if name})


async def _known_accessions(accessions: set[str]) -> set[str]:
    """Which of these accessions are already queued or loaded."""
    if not accessions:
        return set()
    async with session_scope() as session:
        queued = set((await session.execute(
            select(EdgarQueue.accession).where(EdgarQueue.accession.in_(accessions)))).scalars().all())
        loaded = set((await session.execute(
            select(Filing.source_key).where(Filing.source == "edgar", Filing.source_key.in_(accessions))
        )).scalars().all())
    return queued | loaded


async def poll_ownership(ctx: JobContext) -> None:
    """Form 4 and 4/A: the filings the Live page is about. The newest feed page is read every
    20 s in filing hours, even while earlier reads are still waiting (up to 3 in flight).
    owner=only keeps prospectuses out of the feed."""
    await _read_newest(ctx, FORM4_FEEDS, OWNERSHIP_FORMS, inline_limit=20)


async def poll_forms_3_5(ctx: JobContext) -> None:
    """Forms 3 and 5 (first statements and annual reports): less time-critical, every 60 s."""
    await _read_newest(ctx, FORMS_3_5_FEEDS, OWNERSHIP_FORMS, inline_limit=20)


async def poll_form144(ctx: JobContext) -> None:
    """Form 144 notices of proposed sale, every 60 s."""
    await _read_newest(ctx, FORM144_FEEDS, FORM144_FORMS)


async def reach_back_form4(ctx: JobContext) -> None:
    """The Form 4 feed paged back to the watermark (every minute in filing hours): catches a
    burst larger than one page, and anything a failed or cut-short read missed."""
    await _reach_back(ctx, FORM4_FEEDS, OWNERSHIP_FORMS, inline_limit=20, legacy_jobs=("edgar.poll_ownership",))


async def reach_back_other(ctx: JobContext) -> None:
    """The same for Forms 3, 5 and 144 (every 2 minutes in filing hours)."""
    await _reach_back(ctx, FORMS_3_5_FEEDS, OWNERSHIP_FORMS, inline_limit=20,
                      legacy_jobs=("edgar.poll_forms_3_5",))
    await _reach_back(ctx, FORM144_FEEDS, FORM144_FORMS, legacy_jobs=("edgar.poll_form144",))


async def process_queue(ctx: JobContext) -> None:
    """Retry and drain whatever the pollers and reconciliation queued."""
    counts = await run_queue(shared_client(), RawStore(), limit=200, priority=Priority.RECONCILE)
    ctx.rows_new += counts["done"]
    ctx.parse_failures += counts["failed"]
    ctx.stats.update(counts)


async def reconcile_daily_index(ctx: JobContext) -> None:
    """Compare the last few business days' official index against what we have.

    Anything missing is queued (at reconciliation priority) and recorded in
    reconciliation_results, which /status shows. The goal is zero unexplained
    misses.
    """
    client = shared_client()
    now = _now_et()
    # Today's index appears around 22:00-22:30 ET; before that, start from yesterday.
    newest = now.date() if now.time() >= time(22, 45) else now.date() - timedelta(days=1)
    days = [newest - timedelta(days=n) for n in range(0, 5)]
    for day in days:
        if is_sec_business_day(day):
            await _reconcile_day(ctx, client, day, discovered_via="daily_index", priority=1,
                                 lane=Priority.RECONCILE, record=True)


async def retry_daily_index(ctx: JobContext) -> None:
    """Hourly: check again any recent day whose index the SEC hadn't posted at the nightly check.

    The SEC usually posts a day's index by about 22:30 ET, but not always; without a retry
    a missing index waited a whole day, and so did any filing the live feed had missed.
    """
    client = shared_client()
    now = _now_et()
    newest = now.date() if now.time() >= time(22, 45) else now.date() - timedelta(days=1)
    days = [d for d in (newest - timedelta(days=n) for n in range(0, 5)) if is_sec_business_day(d)]
    async with session_scope() as session:
        checked = set((await session.execute(text(
            "SELECT day FROM reconciliation_results WHERE source = :source AND day = ANY(:days)"),
            {"source": COVERAGE_SOURCE, "days": days})).scalars())
    for day in days:
        if day not in checked:
            await _reconcile_day(ctx, client, day, discovered_via="daily_index", priority=1,
                                 lane=Priority.RECONCILE, record=True)
    ctx.stats["days_checked"] = len([d for d in days if d not in checked])


async def _reconcile_day(ctx: JobContext, client: HttpClient, day: date, *, discovered_via: str, priority: int,
                         lane: Priority, record: bool) -> int | None:
    """Queue every Form 3/4/5/144 in one day's official index that we don't hold. Returns how many
    were missing, or None if the index isn't available: not posted (404) or EDGAR didn't answer.
    The run's stats say which, with the HTTP status of the last try (/status shows it)."""
    try:
        fetched = await client.get(daily_index_url(day), priority=lane)
    except FetchError as exc:
        # EDGAR kept failing (e.g. HTTP 503, or no answer at all): say which, so /status can tell
        # "didn't answer" apart from "not posted yet" (404, below).
        ctx.stats[str(day)] = {"error": "no answer", "http_status": fetch_error_status(str(exc))}
        return None
    ctx.docs_fetched += 1
    if fetched.status == 404:
        ctx.stats[str(day)] = {"error": "not posted", "http_status": 404}
        # No filings that day, or not published yet. Days older than a few days have no index
        # because the SEC was closed (an unscheduled closure, such as a national day of mourning),
        # so the day is recorded as having nothing to load.
        if record and day < _today() - timedelta(days=3):
            await _record_check(day, 0, 0, [])
        return None
    indexed = parse_master_index(fetched.content.decode("latin-1"), SUPPORTED_FORMS)
    accessions = {f.accession for f in indexed}
    async with session_scope() as session:
        have = set((await session.execute(
            select(Filing.source_key).where(Filing.source == "edgar", Filing.source_key.in_(accessions))
        )).scalars().all()) if accessions else set()
        # In accession order, like _queue_new: every writer to the queue then takes its row locks
        # in the same order. In index order, a feed read queueing the same filings could
        # deadlock with this (runs 24112 and 24115 on 2026-10-09, when the host woke after days
        # off and the feed, this check and the gap filler all queued the same accessions).
        missing = sorted((f for f in indexed if f.accession not in have), key=lambda f: f.accession)
        for f in missing:
            await enqueue(session, accession=f.accession, form_type=f.form_type, cik=f.ciks[0],
                          accepted_at=None, filed_on=f.filed_on, discovered_via=discovered_via,
                          priority=priority, filer_names=dict(f.names), revive_failed=True)
        if record:
            await _record_check(day, len(accessions), len(have), [f.accession for f in missing], session)
    ctx.stats[str(day)] = {"expected": len(accessions), "missing": len(missing)}
    ctx.rows_new += len(missing)
    return len(missing)


async def _record_check(day: date, expected: int, found: int, missing: list[str],
                        session: AsyncSession | None = None) -> None:
    """Store one day's check against its daily index (see smt.sources.edgar.coverage)."""
    stmt = insert(ReconciliationResult).values(source=COVERAGE_SOURCE, day=day, expected=expected, found=found,
                                               missing=missing)
    stmt = stmt.on_conflict_do_update(
        index_elements=["source", "day"],
        set_={"expected": stmt.excluded.expected, "found": stmt.excluded.found,
              "missing": stmt.excluded.missing, "checked_at": text("now()")})
    if session is not None:
        await session.execute(stmt)
        return
    async with session_scope() as own:
        await own.execute(stmt)


#: Gap filling paces itself: new days are queued only while fewer than this many gap-fill
#: filings are still waiting, so it never floods the queue or the SEC.
GAP_FILL_BACKLOG = 3000
#: A day still short of filings after its queue drained is checked again only after this long
#: (the check revives failed items; a filing that fails for good mustn't cause a fetch every run).
RECHECK_AFTER = timedelta(hours=12)


def _now_et() -> datetime:
    """The time in New York (a function so tests can fix it)."""
    return datetime.now(ET)


def _today() -> date:
    """Today's date in New York."""
    return _now_et().date()


async def fill_gaps(ctx: JobContext) -> None:
    """Load days the site doesn't hold completely, from the SEC's daily indexes (every 10 minutes).

    Between the last quarterly data set and the start of live collection there
    can be weeks with nothing loaded (in 2026: July 1 to early September), and a
    day can be partly held (the Form 3 feed pages back weeks, so a day may have a
    few Form 3s and no Form 4s). Coverage is judged per day by
    smt.sources.edgar.coverage, the same rule GET /api/meta/coverage uses. Every
    business day that isn't loaded is checked against its daily index, newest
    first (the index is fetched on the backfill lane): the check is recorded (so
    the day's share can be tracked) and its missing filings are queued at the
    lowest queue priority, behind live and reconciliation items. process_queue
    then fetches them at Priority.RECONCILE, after live reads, under the shared
    SEC rate limit. They load at full precision and never reach the Live feed.
    A day with filings still waiting in the queue is skipped, and one checked
    in the last RECHECK_AFTER isn't checked again. New days stop being added
    while GAP_FILL_BACKLOG filings are waiting.
    """
    today = _today()
    async with session_scope() as session:
        waiting = int((await session.execute(text(
            "SELECT count(*) FROM edgar_queue WHERE discovered_via = 'gap_fill' AND status = 'pending'"
        ))).scalar_one())
        days = await day_coverage(session, today)
        busy = {r[0] for r in (await session.execute(text(
            "SELECT DISTINCT filed_on FROM edgar_queue WHERE status = 'pending' AND filed_on IS NOT NULL"))).all()}
        recent = {r[0] for r in (await session.execute(text(
            "SELECT day FROM reconciliation_results WHERE source = :source AND checked_at > now() - :age"),
            {"source": COVERAGE_SOURCE, "age": RECHECK_AFTER})).all()}
    not_loaded = [d.day for d in days if d.state != "loaded"]
    # Days from the live period that look complete but were never compared with their index
    # (filled before checks were recorded) are compared once, so coverage rests on a check.
    # The quarterly data sets' days are left alone: the data set is the SEC's own full record.
    async with session_scope() as session:
        dataset_days = {r[0] for r in (await session.execute(text(
            "SELECT DISTINCT filed_on FROM filings WHERE detected_via = 'backfill' AND filed_on IS NOT NULL"
        ))).all()}
    unchecked = [d.day for d in days if d.state == "loaded" and d.expected is None and d.day not in dataset_days]
    todo = not_loaded + unchecked
    ctx.stats["waiting"] = waiting
    ctx.stats["days_left"] = len(not_loaded)
    ctx.stats["unchecked"] = len(unchecked)
    ctx.stats["queued"] = 0
    client = shared_client()
    added = checked = 0
    for day in sorted((d for d in todo if d not in busy and d not in recent), reverse=True):
        if waiting + added >= GAP_FILL_BACKLOG:
            break
        missing = await _reconcile_day(ctx, client, day, discovered_via="gap_fill", priority=3,
                                       lane=Priority.BACKFILL, record=True)
        checked += 1
        added += missing or 0
    ctx.stats["checked"] = checked
    ctx.stats["queued"] = added


async def load_new_dataset(ctx: JobContext) -> None:
    """Daily: load the newest quarterly Insider Transactions data set once the SEC publishes it
    (a no-op when it is already loaded; see smt.sources.edgar.backfill)."""
    from smt.sources.edgar.backfill import backfill_insider_datasets, latest_published_quarter

    year, quarter = latest_published_quarter()
    await backfill_insider_datasets(quarters=[f"{year}q{quarter}"])
    ctx.stats["quarter"] = f"{year}q{quarter}"


async def expire_planned_sales(ctx: JobContext) -> None:
    """Daily: Form 144 notices with no matching Form 4 after 90 days become "no_form4_found".

    That status means only that no Form 4 sale matched within the window
    (smt.ingest.form144); it does not mean the person didn't sell.

    First it settles Form 144 amendments: any Form 4 link still held by a
    notice a 144/A replaced moves to the current version (repair_notice_versions).
    """
    async with session_scope() as session:
        repaired = await repair_notice_versions(session)
        ctx.stats["notice_families_repaired"] = repaired
        ctx.rows_updated += repaired + await expire_unmatched(session)
