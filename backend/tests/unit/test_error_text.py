"""Stored errors turned into plain sentences for the public /status page (smt.worker.error_text)."""

from __future__ import annotations

import re

import pytest

from smt.worker.error_text import (
    ALERT_ERROR_PUBLIC,
    QUEUE_ERROR_CLASS_SQL,
    UNEXPECTED,
    error_class,
    failure_cause,
    fetch_error_status,
    looks_technical,
    plain_alert_error,
    plain_error,
    plain_reason,
)

DAILY_INDEX = "https://www.sec.gov/Archives/edgar/daily-index/2026/QTR3/master.20260924.idx"

#: What the scheduler stores for a job run that failed: the whole traceback.
TRACEBACK = (
    "Traceback (most recent call last):\n"
    '  File "C:\\Users\\someone\\smt\\sources\\edgar\\jobs.py", line 149, in _reconcile_day\n'
    "    fetched = await client.get(daily_index_url(day), priority=lane)\n"
    f"smt.net.client.FetchError: GET {DAILY_INDEX} failed after 5 attempts (HTTP 503)\n"
)


@pytest.mark.parametrize(("stored", "sentence"), [
    (TRACEBACK, "The SEC's daily index for Sep 24 didn't answer (HTTP 503) after 5 tries."),
    (f"FetchError: GET {DAILY_INDEX} failed after 5 attempts (ConnectError: [Errno 11001] getaddrinfo failed)",
     "The SEC's daily index for Sep 24 couldn't be reached after 5 tries (the address lookup failed, which "
     "usually means the internet connection was down)."),
    ("FetchError: GET https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4&owner=only "
     "failed after 5 attempts (ReadTimeout: )",
     "EDGAR's list of the latest Form 4 filings couldn't be reached after 5 tries (no answer in time)."),
    # One of a filing's own files: EDGAR is what didn't answer, and the filing is named after it.
    ("FetchError: GET https://www.sec.gov/Archives/edgar/data/1418226/000119312526396718/index.json "
     "failed after 5 attempts (HTTP 502)",
     "EDGAR didn't answer for filing 0001193125-26-396718 (HTTP 502) after 5 tries."),
    ("FetchError: GET https://www.sec.gov/Archives/edgar/data/1418226/000119312526396718/doc4.xml "
     "failed after 5 attempts (ConnectError: [Errno 10061] refused)",
     "EDGAR couldn't be reached for filing 0001193125-26-396718 after 5 tries (the connection couldn't be made)."),
    ("SecThrottled: SEC returned 403 for https://www.sec.gov/x; SEC traffic paused for 660 s",
     "The SEC refused our requests (HTTP 403)."),
    ("FileNotFoundError: 0001193125-26-396720: index.json not found (yet)",
     "EDGAR didn't have the filing's list of documents yet."),
    ("KeyError: 'reportingOwner'", UNEXPECTED),
    # Our own sentences pass through, tidied.
    ("cancelled (the worker was shutting down, or the run outlived its lease)",
     "Cancelled (the worker was shutting down, or the run outlived its lease)."),
    ("Did not finish: the worker stopped during this run. Closed by the watchdog.",
     "Did not finish: the worker stopped during this run. Closed by the watchdog."),
    (None, "No error text was recorded."),
])
def test_plain_error(stored: str | None, sentence: str) -> None:
    assert plain_error(stored) == sentence


def test_a_database_error_over_several_lines_names_no_sql() -> None:
    # SQLAlchemy's message runs over several lines and ends with a link; the class is on the first.
    stored = ("IntegrityError: (psycopg.errors.UniqueViolation) duplicate key value violates unique constraint\n"
              "DETAIL:  Key (source, source_key)=(edgar, 1) already exists.\n"
              "[SQL: INSERT INTO filings (source) VALUES (%(source)s)]\n"
              "(Background on this error at: https://sqlalche.me/e/20/gkpj)")
    assert plain_error(stored) == UNEXPECTED
    assert error_class(stored) == "IntegrityError"


def test_text_without_a_class_is_shown_only_if_it_reads_as_a_sentence() -> None:
    assert plain_error("Some step failed on https://example.com/x") == UNEXPECTED
    assert plain_error("first line\nsecond line") == UNEXPECTED


def test_fetch_error_status() -> None:
    assert fetch_error_status(f"GET {DAILY_INDEX} failed after 5 attempts (HTTP 503)") == 503
    assert fetch_error_status(f"GET {DAILY_INDEX} failed after 5 attempts (ConnectTimeout: )") is None
    assert fetch_error_status("something else") is None


def test_queue_error_class_pattern_reads_the_start_of_the_stored_error() -> None:
    # The same pattern Postgres runs (smt.worker.watchdog); Python's re reads it the same way here.
    def cls(stored: str) -> str | None:
        match = re.search(QUEUE_ERROR_CLASS_SQL, stored)
        return match.group(1) if match else None

    assert cls("FetchError: GET x failed after 5 attempts (HTTP 503)") == "FetchError"
    assert cls("IntegrityError: (psycopg...) dup\nDETAIL: x\n(Background ...)") == "IntegrityError"
    assert cls("transactionDate: not a date: '2026-13-01'") == "transactionDate"
    assert cls("0001877589-26-000002: no ownershipDocument XML among ['primary_doc.xml']") is None


@pytest.mark.parametrize(("class_name", "cause"), [
    ("FetchError", "outage"),
    ("ConnectError", "outage"),  # httpx.TransportError subclasses
    ("ReadTimeout", "outage"),
    ("SecThrottled", "refused"),  # the SEC answered 403: check the User-Agent before retrying
    ("_DatabaseContention", "busy"),  # our own database, not EDGAR
    ("FileNotFoundError", "other"),
    ("IntegrityError", "other"),
    ("ParseError", "unreadable"),
    ("transactionDate", "unreadable"),  # a parse error stored as its bare message
    (None, "unreadable"),
])
def test_failure_cause(class_name: str | None, cause: str) -> None:
    assert failure_cause(class_name) == cause


@pytest.mark.parametrize("text", [
    "Latest: x (Form 4): smt.net.client.FetchError: GET https://www.sec.gov/x failed",
    "It was started at Sep 23, 10:20 AM ET by worker FrozenwafflesLT:27328.",
    "Latest failure: ValueError: bad",
    "see C:\\Users\\someone\\smt",
    "Traceback (most recent call last):",
])
def test_technical_text_is_recognised(text: str) -> None:
    assert looks_technical(text)


@pytest.mark.parametrize("text", [
    "Latest failure at Sep 22, 11:00 PM ET. The SEC's daily index for Sep 24 didn't answer (HTTP 503) after 5 tries.",
    "1 job run did not finish because the worker stopped (edgar.poll_form144).",
    "Latest: 0001560168-26-000001: 4/A amendment ambiguous: 2 candidate originals; not linked",
    "The owner can put them back in line once EDGAR answers again.",
    "The owner can check the contact address the site sends, then put it back in line.",
])
def test_plain_text_is_left_alone(text: str) -> None:
    assert not looks_technical(text)


def test_plain_reason() -> None:
    assert plain_reason("4/A amendment ambiguous: 2 candidate originals; not linked") == \
        "4/A amendment ambiguous: 2 candidate originals; not linked"
    assert plain_reason("parse error: mismatched tag line 3") == "the document couldn't be read"
    assert plain_reason("ValueError: bad") == "it failed a check while loading"
    assert plain_reason("") == "no reason recorded"


def test_plain_alert_error_hides_the_libraries_text() -> None:
    assert plain_alert_error(None) is None
    assert plain_alert_error("The notification service did not accept the message.") == \
        "The notification service did not accept the message."
    assert plain_alert_error("ConnectError: tgram://123:secret-token/456 unreachable") == ALERT_ERROR_PUBLIC
