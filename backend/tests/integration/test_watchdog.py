"""The watchdog's rules and its alert de-duplication, against the test database.

Every scenario writes scheduler/queue/quarantine rows by hand and runs the
checks at a pinned time, so nothing depends on the clock. Alerts go to a fake
notifier that records messages; nothing is ever sent over the network.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text

from smt.alerts.dispatch import MAX_DELIVERY_ATTEMPTS, dispatch_alerts
from smt.alerts.notifier import Alert
from smt.api.app import create_app
from smt.db.models import IngestionRun, WatchdogFinding
from smt.db.session import session_scope
from smt.worker import watchdog
from smt.worker.error_text import looks_technical
from smt.worker.scheduler import Job, JobContext
from smt.worker.schedules import DailyAt, EdgarHours, Every
from smt.worker.watchdog import CHECKS, CheckFunc, Summary, WatchContext, evaluate

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]

#: Wednesday 2026-09-23, 11:00 AM Eastern: inside EDGAR hours.
WED = datetime(2026, 9, 23, 15, 0, tzinfo=UTC)
#: Sunday 2026-09-27, 11:00 AM Eastern: outside EDGAR hours.
SUN = datetime(2026, 9, 27, 15, 0, tzinfo=UTC)


async def _noop(ctx: JobContext) -> None:
    return None


JOBS: dict[str, Job] = {
    "edgar.poll_ownership": Job("edgar.poll_ownership", "edgar", _noop, EdgarHours(60, 600)),
    "edgar.process_queue": Job("edgar.process_queue", "edgar", _noop, Every(15)),
    "edgar.reconcile_daily_index": Job("edgar.reconcile_daily_index", "edgar", _noop,
                                       DailyAt(time(23, 0), days="all")),
}


class FakeNotifier:
    """Records every alert instead of sending it."""

    delivered_state = "sent"

    def __init__(self, *, accept: bool = True) -> None:
        self.accept = accept
        self.sent: list[Alert] = []

    async def send(self, alert: Alert) -> bool:
        self.sent.append(alert)
        return self.accept


async def _sql(statement: str, **params: Any) -> None:
    async with session_scope() as session:
        await session.execute(text(statement), params)


async def _job(name: str, *, last_ok_at: datetime | None = None, leased_by: str | None = None,
               lease_until: datetime | None = None, updated_at: datetime | None = None) -> None:
    await _sql("INSERT INTO scheduler_jobs (name, last_ok_at, leased_by, lease_until, updated_at) "
               "VALUES (:n, :ok, :by, :until, coalesce(:upd, now()))",
               n=name, ok=last_ok_at, by=leased_by, until=lease_until, upd=updated_at)


async def _run(job: str, status: str, started_at: datetime, error: str | None = None) -> None:
    await _sql("INSERT INTO ingestion_runs (job, source, status, started_at, error) "
               "VALUES (:j, 'edgar', :s, :t, :e)", j=job, s=status, t=started_at, e=error)


async def _evaluate(now: datetime, cursor: dict[str, Any] | None = None,
                    checks: tuple[CheckFunc, ...] = CHECKS) -> Summary:
    async with session_scope() as session:
        return await evaluate(session, now=now, jobs=JOBS, cursor=cursor if cursor is not None else {},
                              stale_after=timedelta(minutes=15), checks=checks)


async def _findings(key: str | None = None) -> list[WatchdogFinding]:
    async with session_scope() as session:
        stmt = select(WatchdogFinding).order_by(WatchdogFinding.id)
        if key:
            stmt = stmt.where(WatchdogFinding.key == key)
        return list((await session.execute(stmt)).scalars().all())


# ---------------------------------------------------------------- stale sources
async def test_stale_poll_during_edgar_hours_is_critical() -> None:
    await _job("edgar.poll_ownership", last_ok_at=WED - timedelta(minutes=20))
    summary = await _evaluate(WED)
    assert summary.opened == 1
    [f] = await _findings()
    assert f.key == "stale_source:edgar.poll_ownership"
    assert f.severity == "critical"
    assert f.title == "No successful poll of the EDGAR Form 4 feed in 20 minutes."
    assert "The last success was at Sep 23, 10:40 AM ET." in f.detail
    assert f.data["edgar_hours"] is True
    assert f.data["limit_minutes"] == 15


async def test_same_silence_outside_edgar_hours_is_fine() -> None:
    # On a Sunday the feed is polled every 10 minutes: 20 minutes without news is normal.
    await _job("edgar.poll_ownership", last_ok_at=SUN - timedelta(minutes=20))
    assert (await _evaluate(SUN)).opened == 0
    # ...but not 50 minutes.
    await _sql("UPDATE scheduler_jobs SET last_ok_at = :t", t=SUN - timedelta(minutes=50))
    assert (await _evaluate(SUN)).opened == 1


async def test_job_that_never_ran_is_not_judged_but_never_succeeding_is() -> None:
    await _job("edgar.poll_ownership")
    assert (await _evaluate(WED)).opened == 0
    await _run("edgar.poll_ownership", "failed", WED - timedelta(minutes=30))
    await _evaluate(WED)
    [stale] = await _findings("stale_source:edgar.poll_ownership")
    assert "It has not succeeded since its first attempt at Sep 23, 10:30 AM ET." in stale.detail


# ---------------------------------------------------------------- de-duplication and alerts
async def test_one_alert_when_a_problem_starts_one_when_it_ends_and_again_if_it_returns() -> None:
    notifier = FakeNotifier()
    await _job("edgar.poll_ownership", last_ok_at=WED - timedelta(minutes=20))

    # Starts: one finding, one message.
    assert (await _evaluate(WED)).opened == 1
    sent = await dispatch_alerts(notifier, status_url="http://localhost:3000/status")
    assert (sent.findings, sent.delivered) == (1, True)
    assert notifier.sent[0].title == "Smart Money Tracker: 1 new issue"
    assert notifier.sent[0].body.startswith("NEW (critical): No successful poll of the EDGAR Form 4 feed")
    assert notifier.sent[0].level == "critical"

    # Still stale 5 minutes later: the numbers refresh, nothing is sent.
    summary = await _evaluate(WED + timedelta(minutes=5))
    assert (summary.opened, summary.still_open) == (0, 1)
    assert (await dispatch_alerts(notifier)).findings == 0
    assert len(notifier.sent) == 1
    [f] = await _findings()
    assert f.open_alert == "sent"
    assert f.last_seen_at == WED + timedelta(minutes=5)
    assert "25 minutes" in f.title

    # Recovers: cleared, one "resolved" message.
    await _sql("UPDATE scheduler_jobs SET last_ok_at = :t", t=WED + timedelta(minutes=9))
    assert (await _evaluate(WED + timedelta(minutes=10))).cleared == 1
    await dispatch_alerts(notifier)
    assert len(notifier.sent) == 2
    assert notifier.sent[1].title == "Smart Money Tracker: 1 resolved"
    assert notifier.sent[1].body.startswith("RESOLVED after 10 minutes: ")
    [f] = await _findings()
    assert (f.cleared_at, f.clear_alert) == (WED + timedelta(minutes=10), "sent")

    # Comes back: a new finding and a new message.
    assert (await _evaluate(WED + timedelta(minutes=40))).opened == 1
    await dispatch_alerts(notifier)
    assert len(notifier.sent) == 3
    rows = await _findings("stale_source:edgar.poll_ownership")
    assert [r.cleared_at is None for r in rows] == [False, True]


async def test_failed_delivery_is_retried_then_marked_failed() -> None:
    await _job("edgar.poll_ownership", last_ok_at=WED - timedelta(minutes=20))
    await _evaluate(WED)
    broken = FakeNotifier(accept=False)
    for attempt in range(1, MAX_DELIVERY_ATTEMPTS + 1):
        result = await dispatch_alerts(broken)
        assert (result.findings, result.delivered) == (1, False)
        [f] = await _findings()
        assert f.alert_attempts == attempt
        assert f.alert_error == "The notification service did not accept the message."
    assert f.open_alert == "failed"
    # Given up: no more attempts, and its end will not be announced either.
    assert (await dispatch_alerts(broken)).findings == 0
    await _sql("UPDATE scheduler_jobs SET last_ok_at = :t", t=WED + timedelta(minutes=1))
    await _evaluate(WED + timedelta(minutes=5))
    [f] = await _findings()
    assert f.clear_alert == "skipped"


async def test_a_notifier_that_raises_does_not_break_dispatch() -> None:
    class Exploding(FakeNotifier):
        async def send(self, alert: Alert) -> bool:
            raise ConnectionError("chat service down")

    await _job("edgar.poll_ownership", last_ok_at=WED - timedelta(minutes=20))
    await _evaluate(WED)
    result = await dispatch_alerts(Exploding())
    assert result.delivered is False
    assert result.error == "ConnectionError: chat service down"
    [f] = await _findings()
    assert (f.open_alert, f.alert_attempts) == ("pending", 1)


async def test_problems_found_in_one_run_go_out_as_one_message() -> None:
    await _job("edgar.poll_ownership", last_ok_at=WED - timedelta(minutes=20))
    for minutes in (3, 2, 1):
        await _run("edgar.process_queue", "failed", WED - timedelta(minutes=minutes), error="boom")
    assert (await _evaluate(WED)).opened == 2
    notifier = FakeNotifier()
    await dispatch_alerts(notifier)
    assert len(notifier.sent) == 1
    assert notifier.sent[0].title == "Smart Money Tracker: 2 new issues"


# ---------------------------------------------------------------- the EDGAR queue
async def _queue(accession: str, *, status: str = "pending", attempts: int = 0,
                 next_attempt_at: datetime, error: str | None = None) -> None:
    await _sql("INSERT INTO edgar_queue (accession, form_type, cik, discovered_via, status, attempts, "
               "next_attempt_at, last_error, discovered_at) VALUES (:a, '4', '0000000001', 'feed', :s, :n, "
               ":t, :e, :t)", a=accession, s=status, n=attempts, t=next_attempt_at, e=error)


async def test_queue_backlog_opens_on_long_waits_and_clears_with_hysteresis() -> None:
    await _queue("0000000001-26-000001", next_attempt_at=WED - timedelta(minutes=40))
    await _evaluate(WED)
    [f] = await _findings("edgar_queue:backlog")
    assert f.title == "EDGAR filings are waiting too long: the oldest has been due for 40 minutes."
    assert f.data["pending"] == 1

    # 15 minutes of waiting would not open it, but it keeps an open finding open.
    await _sql("UPDATE edgar_queue SET next_attempt_at = :t", t=WED + timedelta(minutes=5) - timedelta(minutes=15))
    assert (await _evaluate(WED + timedelta(minutes=5))).cleared == 0
    # Below both clear thresholds: cleared.
    await _sql("UPDATE edgar_queue SET next_attempt_at = :t", t=WED + timedelta(minutes=10) - timedelta(minutes=5))
    assert (await _evaluate(WED + timedelta(minutes=10))).cleared == 1


async def test_a_growing_pile_of_pending_filings_opens_a_backlog() -> None:
    # 500 pending but not yet due (retry backoff): opens only once the count is seen growing.
    await _sql("INSERT INTO edgar_queue (accession, form_type, cik, discovered_via, next_attempt_at) "
               "SELECT 'x-' || g, '4', '0000000001', 'feed', :t FROM generate_series(1, 500) g",
               t=WED + timedelta(hours=1))
    cursor: dict[str, Any] = {"queue_pending": 480}
    await _evaluate(WED, cursor)
    [f] = await _findings("edgar_queue:backlog")
    assert f.title == "500 EDGAR filings are waiting to be processed."
    assert "At the previous check there were 480." in f.detail
    assert cursor["queue_pending"] == 500


#: What smt.sources.edgar.pipeline stores for each kind of failure (exception class, then message;
#: parse errors as the bare message).
OUTAGE_ERROR = ("FetchError: GET https://www.sec.gov/Archives/edgar/data/1234/000119312526396718/index.json "
                "failed after 5 attempts (HTTP 503)")
PARSE_ERROR = "transactionDate: not a date: '2026-13-01'"
OTHER_ERROR = "FileNotFoundError: 0001193125-26-396720: index.json not found (yet)"
#: The same outage, but the internet connection here was down (FetchError wraps that too).
OFFLINE_ERROR = ("FetchError: GET https://www.sec.gov/Archives/edgar/data/1234/000119312526396716/index.json "
                 "failed after 5 attempts (ConnectError: [Errno 11001] getaddrinfo failed)")
THROTTLED_ERROR = "SecThrottled: SEC returned 403 for https://www.sec.gov/x; SEC traffic paused for 660 s"
BUSY_ERROR = "_DatabaseContention: deadlock detected\nDETAIL:  Process 1 waits for ShareLock on transaction 2"


async def test_every_failed_filing_is_reported_by_cause() -> None:
    # Given up after 3 days of transient errors: their attempts were refunded, so they sit far
    # below MAX_ATTEMPTS. They used to be invisible to the watchdog.
    await _queue("0001193125-26-396718", status="failed", attempts=1, next_attempt_at=WED, error=OUTAGE_ERROR)
    await _queue("0001193125-26-396716", status="failed", attempts=0, next_attempt_at=WED - timedelta(hours=2),
                 error=OFFLINE_ERROR)
    await _queue("0001193125-26-396717", status="failed", attempts=0, next_attempt_at=WED - timedelta(hours=1),
                 error=THROTTLED_ERROR)
    await _queue("0001193125-26-396715", status="failed", attempts=0, next_attempt_at=WED - timedelta(hours=3),
                 error=BUSY_ERROR)
    await _queue("0001193125-26-396719", status="failed", attempts=1, next_attempt_at=WED, error=PARSE_ERROR)
    await _queue("0001193125-26-396720", status="failed", attempts=6, next_attempt_at=WED, error=OTHER_ERROR)
    await _queue("0001193125-26-396721", status="pending", attempts=1, next_attempt_at=WED + timedelta(minutes=5),
                 error=OUTAGE_ERROR)  # still being retried: not given up on
    summary = await _evaluate(WED)
    assert summary.opened == 5

    [outage] = await _findings("edgar_queue:outage")
    assert outage.title == "2 EDGAR filings couldn't be fetched for 3 days and were set aside."
    # The latest filing is named once, not twice in a row.
    assert outage.detail == (
        "EDGAR didn't answer, or the connection to it failed. "
        "Such filings are retried every few minutes and set aside once they have waited 3 days. "
        "Latest (Form 4): EDGAR didn't answer for filing 0001193125-26-396718 (HTTP 503) after 5 tries. "
        "The owner can put them back in line once EDGAR answers again.")
    assert outage.data["count"] == 2
    assert outage.data["cause"] == "outage"
    assert [e["accession"] for e in outage.data["examples"]] == ["0001193125-26-396718", "0001193125-26-396716"]

    # A 403 isn't an outage: the remedy starts with the User-Agent setting.
    [refused] = await _findings("edgar_queue:refused")
    assert refused.title == ("1 EDGAR filing couldn't be fetched because the SEC refused our requests (HTTP 403), "
                             "and was set aside.")
    assert refused.detail == (
        "The SEC refuses requests that come too fast or don't give a contact address in the User-Agent. "
        "Such filings are retried every few minutes and set aside once they have waited 3 days. "
        "Latest: 0001193125-26-396717 (Form 4). "
        "The owner can check the contact address the site sends, then put it back in line.")

    # A busy database isn't an SEC outage either.
    [busy] = await _findings("edgar_queue:busy")
    assert busy.title == "1 EDGAR filing couldn't be saved because the database was busy, and was set aside."
    assert busy.detail.endswith("Latest: 0001193125-26-396715 (Form 4). "
                                "The owner can put it back in line.")

    [unreadable] = await _findings("edgar_queue:unreadable")
    assert unreadable.title == "1 EDGAR filing couldn't be read and was set aside."
    assert "The owner can put it back in line once the reader is fixed." \
        in unreadable.detail

    [other] = await _findings("edgar_queue:gave_up")
    assert other.title == "1 EDGAR filing couldn't be loaded after 6 tries and was set aside."
    assert other.detail == ("Latest: 0001193125-26-396720 (Form 4). EDGAR didn't have the filing's list of "
                            "documents yet. The owner can put it back in line.")

    for f in await _findings():
        assert not looks_technical(f"{f.title} {f.detail}"), f.key

    # `smt queue retry` puts them back in line: every finding clears.
    await _sql("UPDATE edgar_queue SET status = 'pending', attempts = 0, last_error = NULL WHERE status = 'failed'")
    assert (await _evaluate(WED + timedelta(minutes=5))).cleared == 5


async def test_a_failed_filing_loaded_another_way_is_not_reported() -> None:
    # Given up on in the queue, then loaded by the SEC's quarterly data set, which writes the
    # filing by accession without touching the queue: nothing is missing any more.
    await _queue("0001193125-26-396718", status="failed", attempts=1, next_attempt_at=WED, error=OUTAGE_ERROR)
    assert (await _evaluate(WED)).opened == 1
    await _sql("INSERT INTO filings (source, source_key, form_type, filed_on, url, detected_via) "
               "VALUES ('edgar', '0001193125-26-396718', '4', '2026-09-23', 'https://www.sec.gov/x', 'backfill')")
    assert (await _evaluate(WED + timedelta(minutes=5))).cleared == 1
    assert (await _evaluate(WED + timedelta(minutes=10))).opened == 0


async def test_an_open_finding_gets_the_new_wording_when_seen_again() -> None:
    # A finding opened by an older version, with the technical text it used to store.
    await _sql("INSERT INTO watchdog_findings (key, check_name, kind, severity, title, detail, data, opened_at, "
               "last_seen_at, open_alert, clear_alert) VALUES ('edgar_queue:outage', 'edgar_queue', 'condition', "
               "'warning', 'old title', :detail, CAST(:data AS jsonb), :t, :t, 'sent', 'none')",
               detail=f"Latest: x (Form 4): smt.net.client.{OUTAGE_ERROR}", data='{"host": "Box:1"}', t=WED)
    await _queue("0001193125-26-396718", status="failed", attempts=1, next_attempt_at=WED, error=OUTAGE_ERROR)
    summary = await _evaluate(WED + timedelta(minutes=5))
    assert (summary.opened, summary.still_open) == (0, 1)
    [f] = await _findings("edgar_queue:outage")
    assert f.title.startswith("1 EDGAR filing couldn't be fetched")
    assert "https://" not in f.detail
    assert "FetchError" not in f.detail
    assert "host" not in f.data
    assert f.open_alert == "sent"  # refreshed text, but no second alert


# ---------------------------------------------------------------- quarantine
async def test_new_quarantine_rows_open_a_finding_that_clears_six_hours_after_the_last() -> None:
    await _sql("INSERT INTO quarantine (source, filing_ref, reason, created_at) "
               "VALUES ('edgar', '0000000001-26-000001', 'missing issuerCik', :t)", t=WED - timedelta(hours=1))
    await _evaluate(WED)
    [f] = await _findings("quarantine:new")
    assert f.title == "1 document set aside for review (quarantined) in the last 6 hours."
    assert "Latest: 0000000001-26-000001: missing issuerCik" in f.detail
    # The row is an hour old at WED, so it leaves the six-hour window at WED + 5 hours.
    assert (await _evaluate(WED + timedelta(hours=4, minutes=59))).cleared == 0
    assert (await _evaluate(WED + timedelta(hours=5))).cleared == 1


# ---------------------------------------------------------------- leases and abandoned runs
async def test_expired_lease_must_be_seen_twice() -> None:
    await _job("edgar.process_queue", last_ok_at=WED - timedelta(minutes=1), leased_by="FrozenwafflesLT:27328",
               lease_until=WED - timedelta(minutes=10), updated_at=WED - timedelta(minutes=40))
    cursor: dict[str, Any] = {}
    assert (await _evaluate(WED, cursor)).opened == 0
    assert cursor["seen"] == {"lease:edgar.process_queue": 1}
    assert (await _evaluate(WED + timedelta(minutes=5), cursor)).opened == 1
    [f] = await _findings("lease:edgar.process_queue")
    # The public text never names the owner's machine or process (that goes to the log).
    assert f.detail == ("It was started at Sep 23, 10:20 AM ET by a worker that has since stopped "
                        "(or is stuck on this job).")
    assert "Frozenwaffles" not in f.detail + f.title + str(f.data)
    assert f.data["same_worker"] is False
    # Released: cleared.
    await _sql("UPDATE scheduler_jobs SET leased_by = NULL, lease_until = NULL")
    assert (await _evaluate(WED + timedelta(minutes=10), cursor)).cleared == 1


async def test_runs_left_running_by_a_stopped_worker_are_closed_and_recorded() -> None:
    await _job("edgar.process_queue", last_ok_at=WED - timedelta(minutes=1))  # released, no lease
    await _run("edgar.process_queue", "running", WED - timedelta(minutes=30))
    await _run("edgar.process_queue", "running", WED - timedelta(seconds=30))  # too recent to judge
    summary = await _evaluate(WED)
    assert summary.events == 1
    async with session_scope() as session:
        runs = (await session.execute(select(IngestionRun).order_by(IngestionRun.id))).scalars().all()
    assert [r.status for r in runs] == ["failed", "running"]
    assert (runs[0].error or "").startswith("Did not finish")
    [event] = await _findings()
    assert (event.kind, event.severity, event.open_alert) == ("event", "info", "skipped")
    assert event.cleared_at == WED
    # The public title names the job in words, not by its key.
    assert event.title == "1 job run did not finish because the worker stopped (EDGAR filing processor)."
    # One run reads "It is", not "They are" (round 6: the page read "1 job run ... They are ...").
    assert event.detail == "It is now recorded as not finished. Each job runs again on its normal schedule."
    # Info events are shown on /status but never pushed.
    assert (await dispatch_alerts(FakeNotifier())).findings == 0


async def test_several_abandoned_runs_are_described_in_the_plural() -> None:
    await _job("edgar.process_queue", last_ok_at=WED - timedelta(minutes=1))
    await _run("edgar.process_queue", "running", WED - timedelta(minutes=40))
    await _run("edgar.process_queue", "running", WED - timedelta(minutes=30))
    await _evaluate(WED)
    [event] = await _findings()
    assert event.title == "2 job runs did not finish because the worker stopped (EDGAR filing processor)."
    assert event.detail == "They are now recorded as not finished. Each job runs again on its normal schedule."


# ---------------------------------------------------------------- failed runs
async def test_frequent_job_needs_three_failures_in_a_row() -> None:
    await _run("edgar.process_queue", "ok", WED - timedelta(minutes=4))
    await _run("edgar.process_queue", "failed", WED - timedelta(minutes=3), error="x\nValueError: bad")
    await _run("edgar.process_queue", "failed", WED - timedelta(minutes=2), error="x\nValueError: bad")
    assert (await _evaluate(WED)).opened == 0
    await _run("edgar.process_queue", "failed", WED - timedelta(minutes=1), error="x\nValueError: bad")
    await _evaluate(WED)
    [f] = await _findings("job_failed:edgar.process_queue")
    assert f.title == "The last 3 runs of the EDGAR filing processor failed."
    assert f.severity == "warning"
    # The error's class and message stay in the run record and the log; the page gets a sentence.
    assert f.detail == ("Latest failure at Sep 23, 10:59 AM ET. It stopped with an unexpected error. "
                        "The full error is in the worker's log.")
    # A success clears it.
    await _run("edgar.process_queue", "ok", WED + timedelta(minutes=1))
    assert (await _evaluate(WED + timedelta(minutes=5))).cleared == 1


async def test_daily_job_failing_once_counts_and_sec_throttling_is_critical() -> None:
    await _run("edgar.reconcile_daily_index", "failed", WED - timedelta(hours=12),
               error="Traceback\nsmt.net.client.SecThrottled: SEC returned 403 for https://www.sec.gov/x")
    await _evaluate(WED)
    [f] = await _findings("job_failed:edgar.reconcile_daily_index")
    assert f.title == "The last run of the nightly EDGAR daily-index check failed."
    assert f.severity == "critical"
    assert f.detail == ("Latest failure at Sep 22, 11:00 PM ET. The SEC refused our requests (HTTP 403). "
                        "The SEC answers 403 when it gets too many requests or no User-Agent. "
                        "All SEC requests pause for 11 minutes.")


async def test_failed_run_details_are_plain_sentences() -> None:
    # The traceback the scheduler stores for a daily-index fetch that kept getting 503s.
    await _run("edgar.reconcile_daily_index", "failed", WED - timedelta(hours=12), error=(
        "Traceback (most recent call last):\n"
        '  File "C:\\Users\\someone\\smt\\sources\\edgar\\jobs.py", line 149, in _reconcile_day\n'
        "smt.net.client.FetchError: GET https://www.sec.gov/Archives/edgar/daily-index/2026/QTR3/"
        "master.20260924.idx failed after 5 attempts (HTTP 503)\n"))
    await _evaluate(WED)
    [f] = await _findings("job_failed:edgar.reconcile_daily_index")
    assert f.detail == ("Latest failure at Sep 22, 11:00 PM ET. "
                        "The SEC's daily index for Sep 24 didn't answer (HTTP 503) after 5 tries.")
    for technical in ("http", "sec.gov", "FetchError", "smt.", "Traceback", "C:\\"):
        assert technical not in f.detail + str(f.data)


# ---------------------------------------------------------------- robustness
async def test_a_broken_check_is_skipped_and_its_findings_stay_open() -> None:
    await _sql("INSERT INTO quarantine (source, reason, created_at) VALUES ('edgar', 'bad', :t)",
               t=WED - timedelta(hours=1))
    await _evaluate(WED)
    assert len(await _findings("quarantine:new")) == 1

    async def broken(session: Any, wc: WatchContext) -> list[watchdog.CheckResult]:
        raise RuntimeError("check exploded")

    async def quarantine_but_broken(session: Any, wc: WatchContext) -> list[watchdog.CheckResult]:
        raise RuntimeError("quarantine check exploded")

    # Six hours later the quarantine finding would clear, but its check is broken: it stays open.
    summary = await _evaluate(WED + timedelta(hours=7), checks=(broken, quarantine_but_broken,
                                                              watchdog.check_stale_sources))
    # The run's numbers are public, so they name the checks only; the errors go to the log.
    assert summary.check_errors == ["broken", "quarantine_but_broken"]
    [f] = await _findings("quarantine:new")
    assert f.cleared_at is None


async def test_run_watchdog_job_records_and_alerts(monkeypatch: pytest.MonkeyPatch) -> None:
    notifier = FakeNotifier()
    monkeypatch.setattr(watchdog, "notifier_from_settings", lambda: notifier)
    await _sql("INSERT INTO quarantine (source, reason) VALUES ('edgar', 'bad XML')")
    ctx = JobContext(job_name="worker.watchdog", run_id=1, cursor={})
    await watchdog.run_watchdog(ctx)
    assert ctx.stats["opened"] == 1
    assert ctx.stats["alerts"]["delivered"] is True
    assert notifier.sent[0].body.splitlines()[-1].endswith("/status")


async def test_status_api_lists_open_and_recent_findings() -> None:
    await _sql("INSERT INTO quarantine (source, reason, created_at) VALUES ('edgar', 'bad', now())")
    await _job("edgar.poll_ownership", last_ok_at=datetime.now(UTC) - timedelta(minutes=5))
    now = datetime.now(UTC)
    await _evaluate(now)
    # Resolve it a minute later so it shows under "recent".
    await _sql("UPDATE quarantine SET created_at = now() - interval '7 hours'")
    await _evaluate(now + timedelta(minutes=1))
    await _sql("INSERT INTO quarantine (source, reason, created_at) VALUES ('edgar', 'worse', now())")
    await _evaluate(now + timedelta(minutes=2))

    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/status")
    assert response.status_code == 200
    report = response.json()["watchdog"]
    assert report["alert_channel"] in ("log", "apprise")
    assert [f["key"] for f in report["open"]] == ["quarantine:new"]
    assert report["open"][0]["cleared_at"] is None
    assert [f["key"] for f in report["recent"]] == ["quarantine:new"]
    assert report["recent"][0]["check"] == "quarantine"
    assert report["recent"][0]["open_alert"] == "pending"


# ---------------------------------------------------------------- stale daily prices
PRICES_JOBS = {**JOBS, "prices.refresh_daily": Job("prices.refresh_daily", "prices", _noop,
                                                   DailyAt(time(17, 30), days="trading"))}


async def _traded_security(security_id: int, traded_on: date, closes: list[date]) -> None:
    """A security an insider traded on `traded_on`, with a stored close on each of `closes`."""
    await _sql("INSERT INTO securities (id, name) VALUES (:id, :n)", id=security_id, n=f"Test Co {security_id}")
    await _sql("INSERT INTO entities (id, kind, name, sort_name, slug) VALUES (:id, 'person', 'A B', 'B A', :s)",
               id=security_id, s=f"a-b-{security_id}")
    await _sql("INSERT INTO filings (id, source, source_key, form_type, url, detected_via) "
               "VALUES (:id, 'edgar', :k, '4', 'https://www.sec.gov/x', 'feed')", id=security_id, k=f"acc-{security_id}")
    await _sql("INSERT INTO transactions (filing_id, source, table_name, line_no, dedupe_key, entity_id, "
               "security_id, direction, traded_on) VALUES (:id, 'form4', 'nonderivative', 1, :k, :id, :id, "
               "'sell', :d)", id=security_id, k=f"tx-{security_id}", d=traded_on)
    for day in closes:
        await _sql("INSERT INTO prices_daily (security_id, day, provider, close) VALUES (:id, :d, 'test', 10)",
                   id=security_id, d=day)


async def _prices_check(now: datetime) -> Summary:
    async with session_scope() as session:
        return await evaluate(session, now=now, jobs=PRICES_JOBS, cursor={}, stale_after=timedelta(minutes=15),
                              checks=(watchdog.check_stale_prices,))


async def test_daily_prices_that_stopped_updating_open_a_finding() -> None:
    """Every series stopped at 2026-09-18 while the price job kept finishing 'partial' (a success).
    Here the only series' newest close is Thursday 9/17."""
    await _traded_security(1, date(2026, 9, 10), [date(2026, 9, 17)])
    # Wednesday 9/23 at 11 AM ET: the last closed session is Tuesday 9/22, three sessions after 9/17.
    assert (await _prices_check(WED)).opened == 1
    [f] = await _findings("prices_stale")
    # One series: "the one", and "It stops", not "1 of 1" and "They all stop" (pre-review 7).
    assert f.title == ("Daily prices are more than 2 trading sessions behind for the one recently traded "
                       "security.")
    assert f.detail.startswith("It stops at Sep 17, 2026, 3 trading sessions ago.")
    assert f.data == {"securities_behind": 1, "securities_measured": 1, "newest_close": "2026-09-17",
                      "sessions_behind": 3, "last_session": "2026-09-22"}
    # Up to two sessions behind is within the provider's usual lag; newer closes clear it.
    await _sql("INSERT INTO prices_daily (security_id, day, provider, close) VALUES (1, :d, 'test', 10)",
               d=date(2026, 9, 21))
    assert (await _prices_check(WED)).cleared == 1


async def test_stale_prices_are_counted_per_security() -> None:
    """Round 6: the check used the newest close of any series, so one current series hid every
    series still behind (here five that stop on Aug 13). Now each recently traded security is
    measured on its own. Securities no insider traded recently aren't measured: the 183 real
    series ending 2026-08-13 are preferred shares and second classes nobody traded, so they
    never open this finding (security 27, last traded in June, is left out the same way)."""
    for n in range(1, 21):   # 20 current series
        await _traded_security(n, date(2026, 9, 1), [date(2026, 9, 21)])
    for n in range(21, 26):  # 5 that stopped on Aug 13
        await _traded_security(n, date(2026, 9, 1), [date(2026, 8, 13)])
    await _traded_security(26, date(2026, 9, 1), [])                    # no provider has it: not behind
    await _traded_security(27, date(2026, 6, 1), [date(2026, 7, 1)])   # not traded in the last 90 days
    assert (await _prices_check(WED)).opened == 1
    [f] = await _findings("prices_stale")
    assert f.title == ("Daily prices are more than 2 trading sessions behind for 5 of 25 recently traded "
                       "securities.")
    # Aug 14 to Sep 22 holds 27 NYSE sessions (Labor Day, Sep 7, is closed).
    assert f.detail.startswith("They all stop at Aug 13, 2026, 27 trading sessions ago.")
    assert (f.data["securities_behind"], f.data["securities_measured"]) == (5, 25)
    # Four catch up. The one left (a delisted ticker, say) is under 5% of the series: cleared.
    await _sql("INSERT INTO prices_daily (security_id, day, provider, close) "
               "SELECT id, DATE '2026-09-21', 'test', 10 FROM securities WHERE id BETWEEN 21 AND 24")
    assert (await _prices_check(WED)).cleared == 1


async def test_stale_prices_name_the_most_common_stop() -> None:
    """The detail names the date most lagging series stop at: usually when the provider stopped."""
    for n in range(1, 4):
        await _traded_security(n, date(2026, 9, 1), [date(2026, 9, 17)])
    await _traded_security(4, date(2026, 9, 1), [date(2026, 8, 13)])
    await _traded_security(5, date(2026, 9, 1), [date(2026, 9, 22)])
    await _prices_check(WED)
    [f] = await _findings("prices_stale")
    assert f.title.endswith("for 4 of 5 recently traded securities.")
    assert f.detail.startswith("3 of them stop at Sep 17, 2026, 3 trading sessions ago.")


async def test_stale_prices_behind_for_every_series_say_all() -> None:
    """Pre-review 7: the live finding read "behind for 475 of 475 recently traded securities. 472
    of them stop at Sep 18, 2026". Every measured series behind reads "all 475"."""
    for n in range(1, 3):
        await _traded_security(n, date(2026, 9, 1), [date(2026, 9, 17)])
    await _traded_security(3, date(2026, 9, 1), [date(2026, 8, 13)])
    await _prices_check(WED)
    [f] = await _findings("prices_stale")
    assert f.title == "Daily prices are more than 2 trading sessions behind for all 3 recently traded securities."
    assert f.detail.startswith("2 of them stop at Sep 17, 2026, 3 trading sessions ago.")


async def test_one_stale_series_among_several_reads_it_stops() -> None:
    """A single lagging series is "It stops at", not "They all stop at"."""
    await _traded_security(1, date(2026, 9, 1), [date(2026, 9, 17)])
    for n in range(2, 6):
        await _traded_security(n, date(2026, 9, 1), [date(2026, 9, 22)])
    await _prices_check(WED)
    [f] = await _findings("prices_stale")
    assert f.title.endswith("for 1 of 5 recently traded securities.")
    assert f.detail.startswith("It stops at Sep 17, 2026, 3 trading sessions ago.")
