"""The EDGAR pipeline end to end, with the SEC mocked: queue -> index.json -> XML -> raw store -> load."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import func, select, text

from smt.db.models import EdgarQueue, Filing, Quarantine, RawDocument, Transaction
from smt.db.session import session_scope
from smt.net.client import HttpClient
from smt.net.rawstore import RawStore
from smt.sources.edgar.pipeline import enqueue, process_queue

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]
FIX = Path(__file__).resolve().parents[1] / "fixtures" / "edgar"
DELL = "0001193125-26-396718"
BASE = "https://www.sec.gov/Archives/edgar/data/1418226/000119312526396718"


async def _no_sleep(*_a: object, **_k: object) -> None:
    return None


@pytest.fixture(autouse=True)
def fast_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(HttpClient, "_backoff", staticmethod(_no_sleep))


def _index(*names: str) -> bytes:
    items = [{"name": n, "last-modified": "2026-09-21 16:30:41", "type": "text.gif"} for n in names]
    return json.dumps({"directory": {"item": items, "name": BASE}}).encode()


async def _queue_dell() -> None:
    async with session_scope() as session:
        await enqueue(session, accession=DELL, form_type="4", cik="0001418226",
                      accepted_at=datetime(2026, 9, 21, 20, 30, 41, tzinfo=UTC),
                      filed_on=date(2026, 9, 21), discovered_via="feed")


@respx.mock
async def test_queued_filing_is_fetched_stored_parsed_and_loaded(raw_dir: Path) -> None:
    respx.get(f"{BASE}/index.json").mock(return_value=httpx.Response(
        200, content=_index(f"{DELL}-index-headers.html", "ownership.xml")))
    respx.get(f"{BASE}/ownership.xml").mock(return_value=httpx.Response(
        200, content=(FIX / "ownership" / f"{DELL}.xml").read_bytes()))
    await _queue_dell()
    async with HttpClient() as client:
        counts = await process_queue(client, RawStore(raw_dir))
    assert counts["done"] == 1
    assert counts["events"] == 18
    async with session_scope() as session:
        status = (await session.execute(select(EdgarQueue.status))).scalar_one()
        raw_roles = sorted((await session.execute(select(RawDocument.doc_role))).scalars().all())
        parsed = (await session.execute(select(RawDocument.parse_status)
                                        .where(RawDocument.doc_role == "primary"))).scalar_one()
        filing = (await session.execute(select(Filing))).scalar_one()
        rows = (await session.execute(select(func.count()).select_from(Transaction))).scalar_one()
    assert status == "done"
    assert raw_roles == ["index", "primary"]      # raw first: both documents stored
    assert parsed == "parsed"
    assert filing.detected_via == "feed"
    assert filing.url.endswith(f"{DELL}-index.htm")
    assert rows == 18


@respx.mock
async def test_wrong_document_is_quarantined_not_dropped(raw_dir: Path) -> None:
    respx.get(f"{BASE}/index.json").mock(return_value=httpx.Response(200, content=_index("ownership.xml")))
    respx.get(f"{BASE}/ownership.xml").mock(return_value=httpx.Response(
        200, content=b"<ownershipDocument><documentType>4</documentType></ownershipDocument>"))
    await _queue_dell()
    async with HttpClient() as client:
        counts = await process_queue(client, RawStore(raw_dir))
    assert counts["failed"] == 1
    async with session_scope() as session:
        status = (await session.execute(select(EdgarQueue.status))).scalar_one()
        quarantined = (await session.execute(select(func.count()).select_from(Quarantine))).scalar_one()
        raw_status = (await session.execute(select(RawDocument.parse_status)
                                            .where(RawDocument.doc_role == "primary"))).scalar_one()
    assert status == "failed"
    assert quarantined == 1
    assert raw_status == "quarantined"


@respx.mock
async def test_missing_index_is_retried_later(raw_dir: Path) -> None:
    respx.get(f"{BASE}/index.json").mock(return_value=httpx.Response(404))
    # The full submission still exists, so the filing wasn't removed: its index just isn't there yet.
    respx.get(url__regex=r".*\.txt$").mock(return_value=httpx.Response(200, content=b"<SEC-DOCUMENT>"))
    await _queue_dell()
    async with HttpClient() as client:
        counts = await process_queue(client, RawStore(raw_dir))
    assert counts["retry"] == 1
    async with session_scope() as session:
        item = (await session.execute(select(EdgarQueue))).scalar_one()
    assert item.status == "pending"
    assert item.attempts == 1
    assert "index.json not found" in (item.last_error or "")


async def test_enqueue_skips_filings_already_loaded_or_queued() -> None:
    await _queue_dell()
    async with session_scope() as session:
        again = await enqueue(session, accession=DELL, form_type="4", cik="0001418226", accepted_at=None,
                              filed_on=None, discovered_via="daily_index")
    assert again is False


@respx.mock
async def test_an_sec_outage_does_not_use_up_attempts(raw_dir: Path) -> None:
    """503s from the SEC are the SEC's problem, not the filing's: the item waits and keeps its attempts."""
    respx.get(f"{BASE}/index.json").mock(return_value=httpx.Response(503))
    await _queue_dell()
    async with HttpClient() as client:
        counts = await process_queue(client, RawStore(raw_dir))
    assert counts["retry"] == 1
    async with session_scope() as session:
        item = (await session.execute(select(EdgarQueue))).scalar_one()
    assert item.status == "pending"
    assert item.attempts == 0
    assert "503" in (item.last_error or "")


async def test_reconciliation_revives_a_failed_filing() -> None:
    await _queue_dell()
    async with session_scope() as session:
        item = (await session.execute(select(EdgarQueue))).scalar_one()
        item.status, item.attempts = "failed", 6
        item.discovered_at = datetime(2026, 9, 1, tzinfo=UTC)  # long ago: past the 3-day give-up
    async with session_scope() as session:
        revived = await enqueue(session, accession=DELL, form_type="4", cik="0001418226", accepted_at=None,
                                filed_on=date(2026, 9, 21), discovered_via="daily_index", revive_failed=True)
        again = await enqueue(session, accession=DELL, form_type="4", cik="0001418226", accepted_at=None,
                              filed_on=date(2026, 9, 21), discovered_via="daily_index", revive_failed=True)
    assert revived
    assert not again  # already pending: nothing to revive
    async with session_scope() as session:
        item = (await session.execute(select(EdgarQueue))).scalar_one()
    assert (item.status, item.attempts) == ("pending", 0)
    # Rediscovered: the give-up clock starts again, so one more SEC outage doesn't end it at once.
    assert item.discovered_at > datetime(2026, 9, 2, tzinfo=UTC)


@respx.mock
async def test_reprocess_rebuilds_from_the_raw_store_without_fetching(raw_dir: Path) -> None:
    from smt.sources.edgar.reprocess import reprocess

    respx.get(f"{BASE}/index.json").mock(return_value=httpx.Response(
        200, content=_index(f"{DELL}-index-headers.html", "ownership.xml")))
    respx.get(f"{BASE}/ownership.xml").mock(return_value=httpx.Response(
        200, content=(FIX / "ownership" / f"{DELL}.xml").read_bytes()))
    await _queue_dell()
    async with HttpClient() as client:
        await process_queue(client, RawStore(raw_dir))
    async with session_scope() as session:
        before = (await session.execute(select(func.count()).select_from(Transaction))).scalar_one()
    calls = respx.calls.call_count

    # Nothing is stale yet, so the default selection is empty; --all takes every stored filing.
    assert (await reprocess(store=RawStore(raw_dir))).filings == 0
    result = await reprocess(everything=True, store=RawStore(raw_dir))
    assert result.filings == 1
    assert result.failed == []
    assert result.rows_new == 0  # idempotent: the same rows, updated in place
    async with session_scope() as session:
        after = (await session.execute(select(func.count()).select_from(Transaction))).scalar_one()
    assert after == before
    assert respx.calls.call_count == calls  # no request to the SEC


async def _load_fixture_days() -> None:
    """One Form 4 held on 2026-09-10 (SNOW) and one on 09-21 (DELL)."""
    from smt.ingest.ownership import FilingMeta, load_ownership
    from smt.sources.forms.ownership import PARSER_VERSION, parse_ownership

    for acc, filed in (("0001821737-26-000013", date(2026, 9, 10)), (DELL, date(2026, 9, 21))):
        doc = parse_ownership((FIX / "ownership" / f"{acc}.xml").read_bytes())
        async with session_scope() as session:
            await load_ownership(session, FilingMeta(
                accession=acc, form_type="4", filed_on=filed, accepted_at=None, url="https://www.sec.gov/",
                primary_doc_url=None, detected_via="manual", parser_version=PARSER_VERSION, live=False), doc)


@respx.mock
async def test_fill_gaps_checks_days_that_arent_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sep 10 and 21 hold one Form 4 each (far too few for a business day); the days between hold none."""
    from smt.sources.edgar import jobs
    from smt.sources.edgar.coverage import day_coverage
    from smt.worker.scheduler import JobContext

    monkeypatch.setattr(jobs, "_today", lambda: date(2026, 9, 22))
    await _load_fixture_days()

    def index_for(request: httpx.Request) -> httpx.Response:
        day = request.url.path.rsplit(".", 2)[-2]  # master.20260914.idx -> 20260914
        body = ("Description: Daily Index\n\nCIK|Company Name|Form Type|Date Filed|File Name\n"
                "--------------------------------------------------------------------------------\n"
                f"1234567|Doe Jane|4|{day}|edgar/data/1234567/0001234567-26-{day[-4:]}00.txt\n")
        return httpx.Response(200, content=body.encode())

    respx.get(url__regex=r"https://www\.sec\.gov/Archives/edgar/daily-index/.*").mock(side_effect=index_for)
    ctx = JobContext(job_name="edgar.fill_gaps", run_id=0, cursor={})
    await jobs.fill_gaps(ctx)
    async with session_scope() as session:
        items = (await session.execute(select(EdgarQueue))).scalars().all()
        checked = await day_coverage(session, date(2026, 9, 22))
    # Sep 10, 11, 14-18 and 21: eight business days, newest first, each checked and recorded.
    assert ctx.stats["checked"] == 8
    assert len(items) == 8
    assert {i.discovered_via for i in items} == {"gap_fill"}
    assert {i.priority for i in items} == {3}
    assert [(d.expected, d.found) for d in checked] == [(1, 0)] * 8
    # A second run finds those days still loading and checks nothing again.
    again = JobContext(job_name="edgar.fill_gaps", run_id=0, cursor={})
    await jobs.fill_gaps(again)
    assert (again.stats["checked"], again.stats["queued"]) == (0, 0)


async def test_day_coverage_states_and_the_coverage_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    from smt.api.app import create_app
    from smt.api.routers import meta
    from smt.sources.edgar import coverage

    monkeypatch.setattr(coverage, "MIN_FORM4_UNCHECKED", 1)  # the fixture day's single Form 4 is enough
    monkeypatch.setattr(meta, "_today", lambda: date(2026, 9, 18))
    await _load_fixture_days()
    checks = [
        (date(2026, 9, 14), 100, ["x-1"]),          # 99 of 100 held: loaded
        (date(2026, 9, 15), 4, ["x-2", "x-3"]),     # 2 of 4: partly loaded
        (date(2026, 9, 16), 2, ["x-4"]),            # 1 of 2: partly loaded
        (date(2026, 9, 17), 3, ["x-5", "x-6", "x-7"]),  # none held
    ]
    async with session_scope() as session:
        for day, expected, missing in checks:
            await session.execute(text(
                "INSERT INTO reconciliation_results (source, day, expected, found, missing) "
                "VALUES (:s, :d, :e, 0, CAST(:m AS jsonb))"),
                {"s": coverage.SOURCE, "d": day, "e": expected, "m": json.dumps(missing)})
        days = await coverage.day_coverage(session, date(2026, 9, 18))
    # Sep 10 holds a Form 4 (unchecked, loaded); Sep 11 holds nothing and was never checked.
    assert [(d.day.day, d.state) for d in days] == [
        (10, "loaded"), (11, "missing"), (14, "loaded"), (15, "partial"), (16, "partial"), (17, "missing")]
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        body = (await client.get("/api/meta/coverage")).json()
    assert body["gaps"] == [
        {"first": "2026-09-11", "last": "2026-09-11", "business_days": 1, "state": "missing"},
        {"first": "2026-09-15", "last": "2026-09-16", "business_days": 2, "state": "partial"},
        {"first": "2026-09-17", "last": "2026-09-17", "business_days": 1, "state": "missing"},
    ]
    assert (body["first_filed"], body["last_filed"]) == ("2026-09-10", "2026-09-21")


async def test_claim_batch_can_be_limited_to_given_accessions() -> None:
    from smt.sources.edgar.pipeline import claim_batch

    async with session_scope() as session:
        for acc, via, prio in (("0000000001-26-000001", "gap_fill", 3), ("0000000001-26-000002", "feed", 0)):
            await enqueue(session, accession=acc, form_type="4", cik="1", accepted_at=None,
                          filed_on=date(2026, 9, 24), discovered_via=via, priority=prio)
    claimed = await claim_batch(10, ["0000000001-26-000001"])
    assert [i.accession for i in claimed] == ["0000000001-26-000001"]
    assert [i.accession for i in await claim_batch(10, [])] == []


@respx.mock
async def test_a_poll_processes_only_what_it_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """Backfill work waiting in the queue must not hold up the next Form 4 poll (it once took
    each poll 1-10 minutes, so new filings waited 10-28 minutes)."""
    from smt.sources.edgar import jobs
    from smt.worker.scheduler import JobContext

    async with session_scope() as session:
        await enqueue(session, accession="0000000001-26-000009", form_type="4", cik="1", accepted_at=None,
                      filed_on=date(2026, 8, 3), discovered_via="gap_fill", priority=3)
    feed = (FIX / "atom" / "getcurrent_form4_owner_only.xml").read_bytes()
    empty = b'<?xml version="1.0" encoding="ISO-8859-1" ?><feed xmlns="http://www.w3.org/2005/Atom"></feed>'
    respx.get(url__regex=r".*getcurrent.*start=0&.*").mock(return_value=httpx.Response(200, content=feed))
    respx.get(url__regex=r".*getcurrent.*").mock(return_value=httpx.Response(200, content=empty))
    calls: list[list[str] | None] = []

    async def fake_run_queue(*_a: object, accessions: list[str] | None = None, **_k: object) -> dict[str, int]:
        calls.append(accessions)
        return {"done": 0, "retry": 0, "failed": 0, "events": 0}

    monkeypatch.setattr(jobs, "run_queue", fake_run_queue)
    ctx = JobContext(job_name="edgar.poll_ownership", run_id=0, cursor={})
    await jobs.poll_ownership(ctx)
    assert ctx.stats["discovered"] > 0
    assert len(calls) == 1
    assert calls[0] is not None
    assert "0000000001-26-000009" not in calls[0]
    assert len(calls[0]) == min(20, ctx.stats["discovered"])
    # A poll that finds nothing new processes nothing.
    calls.clear()
    await jobs.poll_ownership(JobContext(job_name="edgar.poll_ownership", run_id=0, cursor={}))
    assert calls == []


def _atom(accessions: list[str], updated: list[str] | None = None) -> bytes:
    """A getcurrent Atom page listing each accession once, as the Issuer entry of a Form 4.
    `updated`: each entry's acceptance time (default: a few seconds after 17:00 on 2026-09-24)."""
    entries = "".join(
        f"<entry><title>4 - Example Corp (0001234567) (Issuer)</title>"
        f'<link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/1234567/'
        f'{a.replace("-", "")}/{a}-index.htm"/>'
        f"<summary type=\"html\"> &lt;b&gt;Filed:&lt;/b&gt; 2026-09-24 &lt;b&gt;AccNo:&lt;/b&gt; {a} </summary>"
        f"<updated>{updated[i] if updated else f'2026-09-24T17:00:0{i % 10}-04:00'}</updated>"
        f'<category scheme="https://www.sec.gov/" label="form type" term="4"/>'
        f"<id>urn:tag:sec.gov,2008:accession-number={a}</id></entry>"
        for i, a in enumerate(accessions))
    return (f'<?xml version="1.0" encoding="ISO-8859-1" ?><feed xmlns="http://www.w3.org/2005/Atom">'
            f"{entries}</feed>").encode()


@respx.mock
async def test_reach_back_keeps_paging_while_a_page_still_has_new_filings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stopping at the first page with a known filing left holes behind it: 13 Form 4s on
    2026-09-24 were found 30 hours later. Paging now goes on while a page has anything new."""
    from smt.sources.edgar import jobs
    from smt.worker.scheduler import JobContext

    page0 = [f"0009990001-26-{n:06d}" for n in range(100)]      # newest first
    page1 = [f"0009990001-26-{n:06d}" for n in range(100, 150)]  # older: the hole
    async with session_scope() as session:  # one filing on the first page is already known
        await enqueue(session, accession=page0[5], form_type="4", cik="1", accepted_at=None,
                      filed_on=date(2026, 9, 24), discovered_via="feed")
    respx.get(url__regex=r".*getcurrent.*start=0&.*").mock(return_value=httpx.Response(200, content=_atom(page0)))
    respx.get(url__regex=r".*getcurrent.*start=100&.*").mock(return_value=httpx.Response(200, content=_atom(page1)))

    async def no_processing(*_a: object, **_k: object) -> dict[str, int]:
        return {"done": 0, "retry": 0, "failed": 0, "events": 0}

    monkeypatch.setattr(jobs, "run_queue", no_processing)
    ctx = JobContext(job_name="edgar.reach_back_form4", run_id=0, cursor={})
    await jobs.reach_back_form4(ctx)
    assert ctx.stats["discovered"] == 99 + 50  # everything on both pages except the known one
    # Once a whole page is known, paging stops: the next poll fetches one page only.
    again = JobContext(job_name="edgar.reach_back_form4", run_id=0, cursor={})
    await jobs.reach_back_form4(again)
    assert again.stats["discovered"] == 0
    assert again.docs_fetched == 1


@respx.mock
async def test_a_reach_back_that_runs_out_of_pages_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pre-review 7: after a long gap (the machine asleep) more than MAX_PAGES pages can lie
    between the watermark and now. Such a run used to move the watermark silently. It still
    moves it to page 0's newest filing, so the next run reads one page instead of every page
    again each minute (the old watermark only sinks deeper and is never reached), but the run
    now records that it ran out of pages and how far back it got: the rest is left to the
    daily-index reconciliation."""
    from smt.domain.business_days import ET
    from smt.sources.edgar import jobs
    from smt.worker.scheduler import JobContext

    events: list[tuple[str, dict[str, object]]] = []

    class Recorder:
        def warning(self, event: str, **fields: object) -> None:
            events.append((event, fields))

    monkeypatch.setattr(jobs, "log", Recorder())
    monkeypatch.setattr(jobs, "MAX_PAGES", 2)
    # 300 Form 4s, newest first, one a minute back from 17:59 (so 100 minutes to a page).
    accepted = [datetime(2026, 9, 24, 21, 59, tzinfo=UTC) - timedelta(minutes=n) for n in range(300)]
    for page in range(3):
        rows = range(page * 100, page * 100 + 100)
        respx.get(url__regex=rf".*getcurrent.*start={page * 100}&.*").mock(return_value=httpx.Response(
            200, content=_atom([f"0009990002-26-{n:06d}" for n in rows],
                               [accepted[n].astimezone(ET).isoformat() for n in rows])))

    async def no_processing(*_a: object, **_k: object) -> dict[str, int]:
        return {"done": 0, "retry": 0, "failed": 0, "events": 0}

    monkeypatch.setattr(jobs, "run_queue", no_processing)
    old_mark = "2026-09-24T09:00:00-04:00"   # behind page 2 too: the gap is wider than MAX_PAGES
    ctx = JobContext(job_name="edgar.reach_back_form4", run_id=0, cursor={"watermarks": {"4": old_mark}})
    await jobs.reach_back_form4(ctx)
    assert ctx.docs_fetched == 2                       # MAX_PAGES, and no further
    assert ctx.stats["discovered"] == 200              # pages 0 and 1; page 2 waits for the daily index
    assert ctx.stats["pages_exhausted"] == ["4"]
    [(event, fields)] = events
    assert event == "reach_back.pages_exhausted"
    assert fields["feed"] == "4"
    assert fields["watermark"] == old_mark
    assert fields["reached_back_to"] == "2026-09-24T14:40:00-04:00"   # page 1's oldest
    assert ctx.cursor["watermarks"]["4"] == "2026-09-24T17:59:00-04:00"   # page 0's newest
    # The next run stops at the first page: everything on it is known and it reaches the
    # watermark. With the old watermark kept it would read both pages again, every minute.
    again = JobContext(job_name="edgar.reach_back_form4", run_id=0, cursor=ctx.cursor)
    await jobs.reach_back_form4(again)
    assert again.docs_fetched == 1
    assert "pages_exhausted" not in again.stats
    assert len(events) == 1


@respx.mock
async def test_a_day_whose_index_was_missing_is_checked_again_hourly(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import datetime as _dt

    from smt.domain.business_days import ET
    from smt.sources.edgar import jobs
    from smt.sources.edgar.coverage import SOURCE
    from smt.worker.scheduler import JobContext

    monkeypatch.setattr(jobs, "_now_et", lambda: _dt(2026, 9, 25, 9, 0, tzinfo=ET))  # Friday morning
    # The nightly check recorded Sep 21-23; Sep 24's index wasn't posted yet at 11 PM.
    async with session_scope() as session:
        for day in (21, 22, 23):
            await session.execute(text(
                "INSERT INTO reconciliation_results (source, day, expected, found, missing) "
                "VALUES (:s, :d, 0, 0, '[]')"), {"s": SOURCE, "d": date(2026, 9, day)})
    fetched: list[str] = []

    def index_for(request: httpx.Request) -> httpx.Response:
        fetched.append(request.url.path.rsplit(".", 2)[-2])
        return httpx.Response(200, content=b"CIK|Company Name|Form Type|Date Filed|File Name\n---\n")

    respx.get(url__regex=r"https://www\.sec\.gov/Archives/edgar/daily-index/.*").mock(side_effect=index_for)
    ctx = JobContext(job_name="edgar.retry_daily_index", run_id=0, cursor={})
    await jobs.retry_daily_index(ctx)
    # The window is the five days up to Sep 24 (Sep 20 is a Sunday): only Sep 24 wasn't recorded.
    assert fetched == ["20260924"]
    assert ctx.stats["days_checked"] == 1


@respx.mock
async def test_a_live_day_never_checked_is_checked_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """A day filled before checks were recorded looks complete; it is compared with its index once."""
    from smt.sources.edgar import coverage, jobs
    from smt.worker.scheduler import JobContext

    monkeypatch.setattr(jobs, "_today", lambda: date(2026, 9, 11))
    monkeypatch.setattr(coverage, "MIN_FORM4_UNCHECKED", 1)  # Sep 10's one Form 4 makes it look complete
    await _load_fixture_days()
    route = respx.get(url__regex=r"https://www\.sec\.gov/Archives/edgar/daily-index/.*").mock(
        return_value=httpx.Response(200, content=b"CIK|Company Name|Form Type|Date Filed|File Name\n---\n"))
    ctx = JobContext(job_name="edgar.fill_gaps", run_id=0, cursor={})
    await jobs.fill_gaps(ctx)
    assert (ctx.stats["days_left"], ctx.stats["unchecked"], ctx.stats["checked"]) == (0, 1, 1)
    assert route.call_count == 1
    again = JobContext(job_name="edgar.fill_gaps", run_id=0, cursor={})
    await jobs.fill_gaps(again)
    assert again.stats["unchecked"] == 0  # recorded now


@respx.mock
async def test_a_hole_behind_a_fully_known_first_page_is_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """A poll cut short left older filings unfetched; the next poll's first page is all known.
    The watermark from the last completed poll makes it page back past the hole."""
    from smt.sources.edgar import jobs
    from smt.worker.scheduler import JobContext

    page0 = [f"0009990002-26-{n:06d}" for n in range(100)]
    hole = [f"0009990002-26-{n:06d}" for n in range(100, 120)]
    async with session_scope() as session:  # the whole first page is known already
        for acc in page0:
            await enqueue(session, accession=acc, form_type="4", cik="1", accepted_at=None,
                          filed_on=date(2026, 9, 24), discovered_via="feed")
    respx.get(url__regex=r".*getcurrent.*start=0&.*").mock(return_value=httpx.Response(200, content=_atom(page0)))
    later = respx.get(url__regex=r".*getcurrent.*start=100&.*").mock(
        return_value=httpx.Response(200, content=_atom(hole)))

    async def no_processing(*_a: object, **_k: object) -> dict[str, int]:
        return {"done": 0, "retry": 0, "failed": 0, "events": 0}

    monkeypatch.setattr(jobs, "run_queue", no_processing)
    # The last completed poll saw filings up to 16:00 ET; this page starts at 17:00.
    ctx = JobContext(job_name="edgar.reach_back_form4", run_id=0,
                     cursor={"watermarks": {"4": "2026-09-24T16:00:00-04:00"}})
    await jobs.reach_back_form4(ctx)
    assert later.called
    assert ctx.stats["discovered"] == len(hole)
    assert ctx.cursor["watermarks"]["4"].startswith("2026-09-24T17:00")  # moved up to this poll's newest


@respx.mock
async def test_a_filing_edgar_removed_is_marked_gone_not_failed(raw_dir: Path) -> None:
    """Two August filings were listed in their day's index but EDGAR later had no index.json and
    no full submission for them: removed after acceptance. They mustn't read as our failures."""
    respx.get(url__regex=r".*/index\.json$").mock(return_value=httpx.Response(404))
    respx.get(url__regex=r".*\.txt$").mock(return_value=httpx.Response(404))
    async with session_scope() as session:
        await enqueue(session, accession="0001192482-26-000435", form_type="4", cik="0001772720",
                      accepted_at=None, filed_on=date(2026, 8, 6), discovered_via="daily_index")
        await enqueue(session, accession="0001192482-26-000999", form_type="4", cik="0001772720",
                      accepted_at=None, filed_on=datetime.now(UTC).date(), discovered_via="feed")
    async with HttpClient() as client:
        counts = await process_queue(client, RawStore(raw_dir))
    async with session_scope() as session:
        status = dict((await session.execute(select(EdgarQueue.accession, EdgarQueue.status))).all())
    assert status["0001192482-26-000435"] == "gone"
    assert status["0001192482-26-000999"] == "pending"  # today's: the index may not be posted yet
    assert counts["gone"] == 1


@respx.mock
async def test_reads_keep_their_cadence_under_a_slow_page_and_process_each_filing_once(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Critic round 6 (P1): with reads in series, one feed page EDGAR took 20-60 s to build held up
    every filing accepted meanwhile (0001669531-26-000007 took 148.7 s). Now reads start on a
    fixed cadence while the slow one is still in flight, a filing a later read finds is processed
    at once, and a filing two reads both see is queued and processed only once."""
    import asyncio

    from smt.sources.edgar import jobs
    from smt.worker.scheduler import Job, Scheduler
    from smt.worker.schedules import Every

    older = [f"0009990003-26-{n:06d}" for n in range(1, 6)]
    newer = "0009990003-26-000000"
    loop = asyncio.get_running_loop()
    requests: list[float] = []
    slow_done: list[float] = []

    async def feed(request: httpx.Request) -> httpx.Response:
        requests.append(loop.time())
        if len(requests) == 1:  # the first read: a page EDGAR is slow to build
            await asyncio.sleep(1.5)
            slow_done.append(loop.time())
            return httpx.Response(200, content=_atom(older))
        return httpx.Response(200, content=_atom([newer, *older]))

    respx.get(url__regex=r".*getcurrent.*start=0&.*").mock(side_effect=feed)
    deeper = respx.get(url__regex=r".*getcurrent.*start=100&.*").mock(return_value=httpx.Response(200))
    processed: list[tuple[float, list[str]]] = []

    async def record(*_a: object, accessions: list[str] | None = None, **_k: object) -> dict[str, int]:
        processed.append((loop.time(), list(accessions or [])))
        return {"done": len(accessions or []), "retry": 0, "failed": 0, "events": 0}

    monkeypatch.setattr(jobs, "run_queue", record)
    sched = Scheduler([Job("edgar.poll_ownership", "edgar", jobs.poll_ownership, Every(0.2),
                           run_at_start=True, max_in_flight=3)], tick_seconds=0.05)
    running = asyncio.create_task(sched.run_forever())
    for _ in range(100):
        await asyncio.sleep(0.05)
        if slow_done and loop.time() > slow_done[0] + 0.3:
            break
    sched.stop()
    await asyncio.wait_for(running, timeout=10)

    assert slow_done
    during_slow = [t for t in requests[1:] if t < slow_done[0]]
    assert len(during_slow) >= 4          # about one read every 0.2 s while the slow page loads
    every = [acc for _t, batch in processed for acc in batch]
    assert sorted(every) == sorted([newer, *older])  # each filing processed exactly once
    assert processed[0][0] < slow_done[0]  # ...and before the slow read came back
    assert not deeper.called              # the reads never page back
    async with session_scope() as session:
        stats = (await session.execute(text(
            "SELECT stats FROM ingestion_runs WHERE job = 'edgar.poll_ownership' ORDER BY id"))).scalars().all()
        queued = (await session.execute(select(func.count()).select_from(EdgarQueue))).scalar_one()
    assert queued == 6
    assert max(s["overlapping"] for s in stats) >= 1


@respx.mock
async def test_a_read_takes_one_page_and_leaves_the_watermark_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """A full first page of new filings no longer makes the Form 4 read page back: that's the
    reach-back job's work, and only the reach-back job moves the watermark."""
    from smt.sources.edgar import jobs
    from smt.worker.scheduler import JobContext

    page0 = [f"0009990004-26-{n:06d}" for n in range(100)]
    respx.get(url__regex=r".*getcurrent.*start=0&.*").mock(return_value=httpx.Response(200, content=_atom(page0)))
    deeper = respx.get(url__regex=r".*getcurrent.*start=100&.*").mock(return_value=httpx.Response(200))

    async def no_processing(*_a: object, **_k: object) -> dict[str, int]:
        return {"done": 0, "retry": 0, "failed": 0, "events": 0}

    monkeypatch.setattr(jobs, "run_queue", no_processing)
    cursor = {"watermarks": {"4": "2026-09-24T16:00:00-04:00"}}
    ctx = JobContext(job_name="edgar.poll_ownership", run_id=0, cursor=cursor)
    await jobs.poll_ownership(ctx)
    assert ctx.docs_fetched == 1
    assert not deeper.called
    assert ctx.stats["discovered"] == 100
    assert ctx.cursor == {"watermarks": {"4": "2026-09-24T16:00:00-04:00"}}


@respx.mock
async def test_reads_and_reach_back_together_process_each_filing_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """The split in one scene: three page-0 reads in flight (one of them slow) and a reach-back
    run, all over the same feed. Every filing, on the first page and in the older hole behind
    it, is queued and processed exactly once; the reach-back moves the watermark and the reads
    leave theirs alone."""
    import asyncio

    from smt.sources.edgar import jobs
    from smt.worker.scheduler import JobContext

    page0 = [f"0009990006-26-{n:06d}" for n in range(100)]
    hole = [f"0009990006-26-{n:06d}" for n in range(100, 120)]
    first_page_reads = 0

    async def newest(request: httpx.Request) -> httpx.Response:
        nonlocal first_page_reads
        first_page_reads += 1
        if first_page_reads == 1:
            await asyncio.sleep(0.5)  # the slow one: the others overtake it
        return httpx.Response(200, content=_atom(page0))

    async def older(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.1)
        return httpx.Response(200, content=_atom(hole))

    respx.get(url__regex=r".*getcurrent.*start=0&.*").mock(side_effect=newest)
    respx.get(url__regex=r".*getcurrent.*start=100&.*").mock(side_effect=older)
    processed: list[str] = []

    async def record(*_a: object, accessions: list[str] | None = None, **_k: object) -> dict[str, int]:
        await asyncio.sleep(0.05)
        processed.extend(accessions or [])
        return {"done": len(accessions or []), "retry": 0, "failed": 0, "events": 0}

    monkeypatch.setattr(jobs, "run_queue", record)
    mark = {"watermarks": {"4": "2026-09-24T16:00:00-04:00"}}
    reads = [JobContext(job_name="edgar.poll_ownership", run_id=0, cursor=json.loads(json.dumps(mark)))
             for _ in range(3)]
    back = JobContext(job_name="edgar.reach_back_form4", run_id=0, cursor=json.loads(json.dumps(mark)))
    await asyncio.gather(*(jobs.poll_ownership(ctx) for ctx in reads), jobs.reach_back_form4(back))

    # Each run processes at most 20 of what it queued (the rest wait for process_queue), so the
    # check is: nothing processed twice, and every filing queued exactly once.
    assert len(processed) == len(set(processed))
    assert set(processed) <= set(page0 + hole)
    async with session_scope() as session:
        queued = (await session.execute(select(func.count()).select_from(EdgarQueue))).scalar_one()
    assert queued == 120
    assert all(ctx.cursor == mark for ctx in reads)                      # the reads: untouched
    assert back.cursor["watermarks"]["4"].startswith("2026-09-24T17:00")  # the reach-back: moved


@respx.mock
async def test_reach_back_starts_from_the_watermark_the_poll_left(monkeypatch: pytest.MonkeyPatch) -> None:
    """The first reach-back run after the switch has no watermark of its own. It takes the one the
    Form 4 poll kept in its cursor, so a hole behind a fully known first page is still found."""
    from smt.sources.edgar import jobs
    from smt.worker.scheduler import JobContext

    page0 = [f"0009990005-26-{n:06d}" for n in range(100)]
    hole = [f"0009990005-26-{n:06d}" for n in range(100, 110)]
    async with session_scope() as session:
        for acc in page0:
            await enqueue(session, accession=acc, form_type="4", cik="1", accepted_at=None,
                          filed_on=date(2026, 9, 24), discovered_via="feed")
        await session.execute(text(
            "INSERT INTO scheduler_jobs (name, cursor) VALUES ('edgar.poll_ownership', CAST(:c AS jsonb))"),
            {"c": json.dumps({"watermarks": {"4": "2026-09-24T16:00:00-04:00"}})})
    respx.get(url__regex=r".*getcurrent.*start=0&.*").mock(return_value=httpx.Response(200, content=_atom(page0)))
    later = respx.get(url__regex=r".*getcurrent.*start=100&.*").mock(
        return_value=httpx.Response(200, content=_atom(hole)))

    async def no_processing(*_a: object, **_k: object) -> dict[str, int]:
        return {"done": 0, "retry": 0, "failed": 0, "events": 0}

    monkeypatch.setattr(jobs, "run_queue", no_processing)
    ctx = JobContext(job_name="edgar.reach_back_form4", run_id=0, cursor={})
    await jobs.reach_back_form4(ctx)
    assert later.called
    assert ctx.stats["discovered"] == len(hole)
    assert ctx.cursor["watermarks"]["4"].startswith("2026-09-24T17:00")  # its own watermark from now on
