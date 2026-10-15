"""The Status page's figures: the Form 4 detection delay, and findings made safe to publish.

Every scenario writes heartbeat runs, filings and findings by hand and measures
at a pinned time, so nothing depends on the clock.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import text

from smt.api.app import create_app
from smt.api.routers.status import (
    TECHNICAL_DETAIL_PUBLIC,
    collected_in_filing_hours,
    detection_delay,
    reconciliation_finds,
    worker_outages,
)
from smt.db.session import session_scope
from smt.domain.business_days import ET
from smt.net.client import HttpClient
from smt.net.ratelimit import Priority
from smt.sources.edgar.daily_index import daily_index_url
from smt.sources.edgar.jobs import _reconcile_day
from smt.worker.error_text import ALERT_ERROR_PUBLIC
from smt.worker.scheduler import JobContext

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]

#: Thursday 2026-09-24, 12:00 PM Eastern.
NOW = datetime(2026, 9, 24, 16, 0, tzinfo=UTC)


def et(day: int, hour: int, minute: int = 0) -> datetime:
    """A time on a day of September 2026, in Eastern time."""
    return datetime(2026, 9, day, hour, minute, tzinfo=ET)


async def _sql(statement: str, **params: Any) -> None:
    async with session_scope() as session:
        await session.execute(text(statement), params)


async def _heartbeats(start: datetime, end: datetime, *, gaps: list[tuple[datetime, datetime]]) -> None:
    """One heartbeat run a minute from start to end (exclusive), none inside each gap."""
    await _sql(
        "INSERT INTO ingestion_runs (job, source, status, started_at) "
        "SELECT 'worker.heartbeat', 'worker', 'ok', t FROM generate_series("
        "CAST(:start AS timestamptz), CAST(:end AS timestamptz) - interval '1 minute', interval '1 minute') t "
        "WHERE NOT EXISTS (SELECT 1 FROM unnest(CAST(:g_from AS timestamptz[]), CAST(:g_to AS timestamptz[])) "
        "                  AS g(a, b) WHERE t >= g.a AND t < g.b)",
        start=start, end=end, g_from=[a for a, _ in gaps], g_to=[b for _, b in gaps])


async def _filing(key: str, accepted: datetime, delay: timedelta, *, form: str = "4", via: str = "feed") -> None:
    await _sql(
        "INSERT INTO filings (source, source_key, form_type, accepted_at, filed_on, url, detected_via, detected_at) "
        "VALUES ('edgar', :k, :f, :a, :d, 'https://www.sec.gov/x', :v, :det)",
        k=key, f=form, a=accepted, d=accepted.date(), v=via, det=accepted + delay)


async def _measure(now: datetime = NOW) -> Any:
    async with session_scope() as session:
        return await detection_delay(session, now)


async def _scenario() -> None:
    """Heartbeats from Wed 10 AM ET to Thu noon, with the worker down Wed 12:00-12:30 PM ET (filing
    hours) and Wed 11 PM - Thu 1 AM ET (outside them)."""
    await _heartbeats(et(23, 10), NOW, gaps=[(et(23, 12), et(23, 12, 30)), (et(23, 23), et(24, 1))])
    await _filing("a", et(23, 10, 30), timedelta(seconds=40))
    await _filing("b", et(23, 11), timedelta(seconds=60))
    await _filing("c", et(23, 11, 30), timedelta(seconds=100))
    await _filing("d", et(23, 13), timedelta(seconds=300))
    await _filing("e", et(23, 14), timedelta(seconds=50), form="4/A")
    # Accepted a minute before the worker stopped (its last beat was 11:59): no fair chance, set apart.
    await _filing("f", et(23, 11, 58), timedelta(minutes=33))
    # Missed by the live feed while the worker ran, found by the nightly check: counted, whole wait.
    await _filing("g", et(23, 14, 30), timedelta(hours=5), via="daily_index")
    # Not measured: another form, outside filing hours, before the worker's first heartbeat.
    await _filing("h", et(23, 15), timedelta(seconds=30), form="3")
    await _filing("i", et(23, 22, 30), timedelta(seconds=600))
    await _filing("j", et(23, 9), timedelta(seconds=900))


async def test_detection_delay_counts_form_4s_in_filing_hours_and_sets_outages_apart() -> None:
    await _scenario()
    d = await _measure()
    assert d.since == et(23, 10)  # the first heartbeat: nothing before it was watched live
    assert d.filings == 6
    assert d.p50_seconds == pytest.approx(80)
    # 95th percentile of 40, 50, 60, 100, 300, 18,000 s (g's 5 hours), interpolated: 300 + 0.75 x 17,700.
    assert d.p95_seconds == pytest.approx(13575)
    assert (d.target_seconds, d.target_met) == (120, False)
    # Only the daytime gap counts: the night one falls outside filing hours.
    assert d.outages == 1
    assert d.outage_seconds == pytest.approx(31 * 60)  # last beat 11:59, next 12:30
    assert d.filings_during_outages == 1
    assert d.found_later == 1
    # The same figures for each business day (all of these were accepted on Wednesday 9/23).
    [wed] = d.days
    assert (wed.day, wed.filings, wed.filings_during_outages, wed.found_later) == (date(2026, 9, 23), 6, 1, 1)
    assert (wed.p50_seconds, wed.p95_seconds, wed.max_seconds) == (pytest.approx(80), pytest.approx(13575), 18000)


async def test_each_day_shows_its_own_figures_newest_first() -> None:
    await _heartbeats(et(23, 10), NOW, gaps=[])
    await _filing("slow", et(23, 11), timedelta(seconds=900))      # Wednesday, before a fix
    await _filing("fast1", et(24, 10), timedelta(seconds=40))      # Thursday, after it
    await _filing("fast2", et(24, 11), timedelta(seconds=70))
    d = await _measure()
    assert [(x.day, x.filings, x.max_seconds) for x in d.days] == [
        (date(2026, 9, 24), 2, 70), (date(2026, 9, 23), 1, 900)]


async def test_the_target_counts_as_met_only_when_the_95th_percentile_is_within_two_minutes() -> None:
    await _scenario()
    await _sql("DELETE FROM filings WHERE source_key IN ('d', 'g')")
    d = await _measure()
    assert d.p95_seconds == pytest.approx(94)  # 40, 50, 60, 100 s, interpolated: 60 + 0.85 x 40
    assert d.target_met is True


async def test_a_worker_that_is_down_now_is_an_outage_up_to_now() -> None:
    await _scenario()
    later = NOW + timedelta(minutes=11)  # the last heartbeat was at 11:59 AM ET
    async with session_scope() as session:
        spans = await worker_outages(session, et(23, 10), later)
    assert spans[-1] == (NOW - timedelta(minutes=1), later)
    assert (await _measure(later)).outages == 2


async def test_a_filing_that_waited_through_a_night_time_stop_is_set_apart() -> None:
    # Accepted at 9:59:55 PM ET, just inside filing hours. The worker stopped right after
    # 10 PM and came back at 5 AM, before filing hours. That stop isn't an outage in filing
    # hours, but the filing waited on it, so its 7-hour delay stays out of the figures.
    await _heartbeats(et(23, 10), NOW, gaps=[(et(23, 22, 1), et(24, 5))])
    await _filing("a", et(23, 11), timedelta(seconds=60))
    await _filing("late", et(23, 21, 59) + timedelta(seconds=55), timedelta(hours=7, minutes=1))  # found 5:00:55
    d = await _measure()
    assert (d.outages, d.outage_seconds) == (0, 0.0)
    assert d.filings_during_outages == 1
    assert (d.filings, d.p95_seconds, d.target_met) == (1, pytest.approx(60), True)


async def test_nothing_to_measure_before_the_worker_has_run() -> None:
    await _filing("a", et(23, 10, 30), timedelta(seconds=40))
    d = await _measure()
    assert (d.since, d.filings, d.p95_seconds, d.target_met, d.outages) == (None, 0, None, None, 0)


# ---------------------------------------------------------------- published findings
async def test_findings_from_older_versions_are_made_safe_to_publish() -> None:
    await _sql(
        "INSERT INTO watchdog_findings (key, check_name, kind, severity, title, detail, data, opened_at, "
        "last_seen_at, open_alert, clear_alert, alert_error) VALUES "
        "('lease:edgar.process_queue', 'lease', 'condition', 'warning', 'The filing processor has run past its "
        "time limit.', 'It was started at Sep 23, 10:20 AM ET by worker FrozenwafflesLT:27328.', "
        "CAST(:data AS jsonb), :t, :t, 'failed', 'none', 'ConnectError: tgram://123:token/456')",
        data='{"job": "edgar.process_queue", "leased_by": "FrozenwafflesLT:27328", '
             '"examples": [{"accession": "x", "error": "FetchError: GET https://www.sec.gov/x failed"}]}',
        t=NOW)
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        report = (await client.get("/api/status")).json()
    [finding] = report["watchdog"]["open"]
    assert finding["detail"] == TECHNICAL_DETAIL_PUBLIC
    assert finding["data"] == {"job": "edgar.process_queue", "examples": [{"accession": "x"}]}
    assert finding["alert_error"] == ALERT_ERROR_PUBLIC
    assert "Frozenwaffles" not in str(report)
    # The detection figures replace the old mixed-form lag.
    assert "pipeline_lag_seconds" not in report
    assert report["detection"]["forms"] == ["4", "4/A"]


# ---------------------------------------------------------------- reconciliation's record of a missing index
async def _no_sleep(*_a: object, **_k: object) -> None:
    return None


@respx.mock
async def test_reconciliation_records_why_an_index_was_not_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(HttpClient, "_backoff", staticmethod(_no_sleep))
    respx.get(daily_index_url(date(2026, 9, 24))).mock(return_value=httpx.Response(503))
    respx.get(daily_index_url(date(2026, 9, 25))).mock(return_value=httpx.Response(404))
    ctx = JobContext(job_name="edgar.reconcile_daily_index", run_id=0, cursor={})
    async with HttpClient() as client:
        for day in (date(2026, 9, 24), date(2026, 9, 25)):
            assert await _reconcile_day(ctx, client, day, discovered_via="daily_index", priority=1,
                                        lane=Priority.RECONCILE, record=False) is None
    assert ctx.stats == {"2026-09-24": {"error": "no answer", "http_status": 503},
                         "2026-09-25": {"error": "not posted", "http_status": 404}}


# ---------------------------------------------------------------- what the nightly check found
_accessions = 0


async def _found(day: date, via: str, accepted: datetime | None, discovered: datetime | None = None) -> None:
    """A filing dated `day` in the queue, discovered by `via`, and loaded with its acceptance time
    (not loaded yet when `accepted` is None)."""
    global _accessions
    _accessions += 1
    accession = f"0000000000-26-{_accessions:06d}"
    await _sql("INSERT INTO edgar_queue (accession, form_type, cik, discovered_via, filed_on, status, discovered_at) "
               "VALUES (:a, '4', '1', :v, :d, :s, coalesce(:t, now()))",
               a=accession, v=via, d=day, s="done" if accepted else "pending", t=discovered)
    if accepted is not None:
        await _sql("INSERT INTO filings (source, source_key, form_type, accepted_at, filed_on, url, detected_via, "
                   "detected_at) VALUES ('edgar', :k, '4', :a, :d, 'https://www.sec.gov/x', :v, :a)",
                   k=accession, a=accepted, d=day, v=via)


async def _finds(days: list[date], now: datetime) -> dict[date, tuple[int | None, int | None]]:
    async with session_scope() as session:
        found = await reconciliation_finds(session, days, now)
    return {d: (f.missed_live, f.while_down) for d, f in found.items()}


async def _first_days_of_live_collection() -> None:
    """Round 6's real case: the worker first ran on Thu 9/24 at 8:04 AM ET. Its first poll paged
    back into Mon-Wed (feed rows for those days, discovered on 9/24), and the nightly check then
    found the rest of those days. None of them was a filing the running feed missed."""
    await _heartbeats(et(24, 8, 4), et(25, 9), gaps=[])
    for day in (21, 22, 23):
        await _found(date(2026, 9, day), "feed", et(day, 15), discovered=et(24, 8, 5))
        await _found(date(2026, 9, day), "daily_index", et(day, 10))
        await _found(date(2026, 9, day), "daily_index", et(day, 21))
    # Thursday: live from 8:04 AM ET.
    await _found(date(2026, 9, 24), "feed", et(24, 9))
    await _found(date(2026, 9, 24), "daily_index", et(24, 7, 30))   # before the worker started
    await _found(date(2026, 9, 24), "daily_index", et(24, 10))      # missed while running
    await _found(date(2026, 9, 24), "gap_fill", et(24, 11))         # the gap filler's find counts too


async def test_finds_before_live_collection_are_not_charged_to_the_live_feed() -> None:
    await _first_days_of_live_collection()
    days = [date(2026, 9, d) for d in (21, 22, 23, 24)]
    finds = await _finds(days, et(25, 9))
    # A feed backlog discovered the day after acceptance doesn't make Mon-Wed live days.
    assert finds[date(2026, 9, 21)] == finds[date(2026, 9, 22)] == finds[date(2026, 9, 23)] == (None, None)
    # Thursday: one gap-filler row doesn't hide the day's real misses (the old rule read
    # "Loaded afterwards"); the 7:30 AM find was before the worker ran.
    assert finds[date(2026, 9, 24)] == (2, 1)


async def test_an_evening_outage_sets_its_finds_apart() -> None:
    """Round 6's Sep 28: the host slept from 4:45 to 11:12 PM ET, and all 148 finds were accepted in
    between. They read as accepted while the worker was down, not as live misses."""
    await _heartbeats(et(24, 8), et(24, 9), gaps=[])                 # an earlier, brief run
    await _heartbeats(et(25, 23), et(25, 23, 30), gaps=[])           # Friday: up only at night
    await _heartbeats(et(28, 5), et(29, 1), gaps=[(et(28, 16, 45), et(28, 23, 12))])
    await _found(date(2026, 9, 25), "daily_index", et(25, 12))
    await _found(date(2026, 9, 28), "feed", et(28, 9))
    await _found(date(2026, 9, 28), "daily_index", et(28, 12))       # missed while running
    await _found(date(2026, 9, 28), "daily_index", et(28, 16, 43))   # a minute before the last beat
    await _found(date(2026, 9, 28), "daily_index", et(28, 17))
    await _found(date(2026, 9, 28), "daily_index", et(28, 21, 58))
    await _found(date(2026, 9, 28), "daily_index", None)             # not loaded yet: still missing
    finds = await _finds([date(2026, 9, 25), date(2026, 9, 28)], et(29, 1))
    # Friday had no uptime in filing hours: its filings were loaded afterwards.
    assert finds[date(2026, 9, 25)] == (None, None)
    assert finds[date(2026, 9, 28)] == (1, 3)


async def test_status_reports_the_split_for_each_checked_day() -> None:
    await _first_days_of_live_collection()
    for day, expected in ((date(2026, 9, 23), 3), (date(2026, 9, 24), 4)):
        await _sql("INSERT INTO reconciliation_results (source, day, expected, found) "
                   "VALUES ('edgar_ownership_144', :d, :e, :e)", d=day, e=expected)
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        rows = (await client.get("/api/status")).json()["reconciliation"]
    assert [(r["day"], r["missing_at_check"], r["found_while_down"], r["missing"]) for r in rows] == [
        ("2026-09-24", 2, 1, 0), ("2026-09-23", None, None, 0)]


async def test_a_day_counts_as_collected_only_with_uptime_in_filing_hours() -> None:
    day = date(2026, 9, 28)
    assert collected_in_filing_hours(day, [], et(29, 1))
    # Down all through 06:00-22:00 ET, up at night: not collected.
    assert not collected_in_filing_hours(day, [(et(28, 5), et(28, 22, 30))], et(29, 1))
    # Up for a minute of filing hours: collected (its finds are then placed one by one).
    assert collected_in_filing_hours(day, [(et(28, 5), et(28, 21, 59))], et(29, 1))
    # A day whose filing hours haven't begun yet.
    assert not collected_in_filing_hours(day, [], et(28, 5))
