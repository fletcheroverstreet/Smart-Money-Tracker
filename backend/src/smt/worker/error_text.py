"""Plain sentences for stored errors, for the public /status page and the owner's alerts.

Errors are stored in full where only the owner can read them: a job run's
traceback in `ingestion_runs.error`, a queued filing's last error in
`edgar_queue.last_error`, and both in the worker's log. What the watchdog and
/status show is one plain sentence built here. Python class names, module
paths, full URLs and machine names never appear in it.

Example: the stored line

    smt.net.client.FetchError: GET https://www.sec.gov/Archives/edgar/daily-index/2026/QTR3/master.20260924.idx
    failed after 5 attempts (HTTP 503)

becomes "The SEC's daily index for Sep 24 didn't answer (HTTP 503) after 5 tries."
"""

from __future__ import annotations

import re
from datetime import date
from typing import Literal
from urllib.parse import parse_qs, urlsplit

import httpx

#: What a stored error says when there isn't a more specific sentence for it.
UNEXPECTED = "It stopped with an unexpected error. The full error is in the worker's log."

#: Errors that mean EDGAR (or the network, or a busy database) didn't answer: not the
#: filing's fault. smt.sources.edgar.pipeline.process_queue catches exactly these
#: (SecThrottled, FetchError, httpx.TransportError and its subclasses, _DatabaseContention),
#: retries them every few minutes without using up an attempt, and gives up only after
#: TRANSIENT_GIVE_UP. Keep the two lists in step.
TRANSIENT_ERRORS: frozenset[str] = frozenset(
    {"SecThrottled", "FetchError", "_DatabaseContention"}
    | {name for name, obj in vars(httpx).items()
       if isinstance(obj, type) and issubclass(obj, httpx.TransportError)}
)

#: Why a queued filing was given up on (smt.worker.watchdog reports each cause separately,
#: because each has a different remedy). See failure_cause.
FailureCause = Literal["outage", "refused", "busy", "unreadable", "other"]

#: For SQL: the class name at the START of a queued filing's stored error. The pipeline
#: stores "<class>: <message>" ("FetchError: GET ... failed"), and the message can run over
#: several lines (a database error adds its SQL and a link), so the class is looked for on the
#: first line only. Parse errors are stored as their bare message, so they have no class
#: name (or only a field name, such as "transactionDate: not a date").
QUEUE_ERROR_CLASS_SQL = r"^(?:[A-Za-z_][A-Za-z0-9_]*\.)*([A-Za-z_][A-Za-z0-9_]*): "

#: What the page says about an alert that couldn't be delivered, unless it is the plain
#: sentence smt.alerts.dispatch writes itself: the notification library's own error could
#: quote the alert service's address, which can hold its access token.
ALERT_ERROR_PUBLIC = "The alert couldn't be delivered. The full error is in the worker's log."
_ALERT_NOT_ACCEPTED = "The notification service did not accept the message."

_URL = re.compile(r"https?://\S+")
#: "smt.net.client.FetchError: message" or a bare "KeyError".
_EXCEPTION_LINE = re.compile(r"^(?:[A-Za-z_]\w*\.)*(?P<cls>[A-Za-z_]\w*)(?:: (?P<msg>.*))?$")
#: What makes a word an exception class rather than the first word of a sentence.
_CLASS_NAME = re.compile(r"_?[A-Z]\w*(?:Error|Exception|Exit|Interrupt|Timeout|Throttled|Contention)")
#: smt.net.client.HttpClient.get's message once every retry has failed.
_FETCH_FAILED = re.compile(r"^GET (?P<url>\S+) failed after (?P<tries>\d+) attempts \((?P<why>.*)\)$")
_HTTP_STATUS = re.compile(r"^HTTP (?P<status>\d{3})$")
#: Signs that a text is technical rather than a sentence for readers: a web address, a
#: traceback, a dotted module path ("smt.net.client"), an exception class name, a machine
#: name with a process id ("MYPC:27328") or a Windows file path. Two-part job keys such as
#: "edgar.poll_form144" are allowed: the page swaps them for plain job names.
#: web/src/components/status/Watchdog.tsx has the same test; keep the two in step.
_TECHNICAL = re.compile(
    r"https?://|www\.|Traceback|\b[a-z_]\w*(?:\.[a-z_]\w*){2,}\b"
    r"|\b_?[A-Z]\w*(?:Error|Exception|Throttled|Contention)\b"
    r"|\b[A-Za-z][\w.-]*:\d{2,}\b|\b[A-Za-z]:\\"
)


def last_line(error: str | None) -> str:
    """The last non-empty line of a stored error (a traceback ends with the error itself)."""
    lines = [ln.strip() for ln in (error or "").splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def looks_technical(text: str | None) -> bool:
    """True when a text holds something a reader shouldn't see (see _TECHNICAL)."""
    return bool(text and _TECHNICAL.search(text))


def _exception_line(error: str | None) -> re.Match[str] | None:
    """The line that names the exception: the lowest line of the form "<Class>: <message>".

    A traceback ends with that line, but a message can run over several lines and notes
    can follow it, so the lines are searched from the bottom up. Frame lines ("File ...")
    and code lines never match, because they contain spaces before any colon.
    """
    for line in reversed([ln.strip() for ln in (error or "").splitlines() if ln.strip()]):
        match = _EXCEPTION_LINE.match(line)
        if match is not None and _CLASS_NAME.fullmatch(match["cls"]):
            return match
    return None


def error_class(error: str | None) -> str | None:
    """The exception class a stored error names ("FetchError"), or None for a plain message."""
    match = _exception_line(error)
    return match["cls"] if match else None


def fetch_error_status(message: str) -> int | None:
    """The HTTP status of the last try, from a FetchError's message ("GET ... failed after 5
    attempts (HTTP 503)"); None when the last try got no answer at all (a timeout, say)."""
    fetch = _FETCH_FAILED.match(message)
    status = _HTTP_STATUS.match(fetch["why"]) if fetch else None
    return int(status["status"]) if status else None


def failure_cause(class_name: str | None) -> FailureCause:
    """Sort a failed queue item by the class its stored error starts with (QUEUE_ERROR_CLASS_SQL).

    The first three are the pipeline's transient errors (TRANSIENT_ERRORS): they don't use
    up an attempt, and the filing is set aside only once it has waited TRANSIENT_GIVE_UP
    (3 days) since it was found. They are told apart because the remedy differs.

    * outage: EDGAR didn't answer, or the connection to it failed (an SEC outage, or the
      internet connection here was down). Retrying once EDGAR answers again is enough.
    * refused: the SEC answered 403 (SecThrottled). It does that to requests that come too
      fast or carry no contact address in the User-Agent, so the setting is checked first.
    * busy: our own database cancelled the load because another update to the same rows
      was running (_DatabaseContention). Not EDGAR's doing.
    * unreadable: the document came back but couldn't be parsed. The pipeline stores
      parse errors as their bare message, so they carry no class name.
    * other: any other error, after the pipeline's MAX_ATTEMPTS tries.
    """
    if class_name == "SecThrottled":
        return "refused"
    if class_name == "_DatabaseContention":
        return "busy"
    if class_name in TRANSIENT_ERRORS:
        return "outage"
    if class_name and class_name != "ParseError" and _CLASS_NAME.fullmatch(class_name):
        return "other"
    return "unreadable"


def plain_error(error: str | None) -> str:
    """One plain sentence for a stored error (a whole traceback, or one line)."""
    lines = [ln.strip() for ln in (error or "").splitlines() if ln.strip()]
    if not lines:
        return "No error text was recorded."
    match = _exception_line(error)
    if match is None:
        # No exception named: a sentence our own code wrote, such as "Did not finish: the
        # worker stopped during this run." Anything longer or more technical isn't shown.
        return _sentence(lines[0]) if len(lines) == 1 and not looks_technical(lines[0]) else UNEXPECTED
    cls, message = match["cls"], match["msg"] or ""
    if cls == "FetchError":
        fetch = _FETCH_FAILED.match(message)
        if fetch is None:
            return "EDGAR didn't answer after several tries."
        return _fetch_sentence(fetch["url"], int(fetch["tries"]), fetch["why"])
    if cls == "SecThrottled":
        return "The SEC refused our requests (HTTP 403)."
    if cls == "_DatabaseContention":
        return "The database was busy with another update to the same rows, so this one waited."
    if cls == "ParseError":
        return "The document couldn't be read."
    if cls == "FileNotFoundError" and "index.json" in message:
        return "EDGAR didn't have the filing's list of documents yet."
    if cls == "OperationalError" and "connection" in message:
        return "The database couldn't be reached."
    if cls in TRANSIENT_ERRORS:  # an httpx connection error that reached us directly
        return f"A connection failed ({_connection_problem(cls, message)})."
    return UNEXPECTED


def plain_reason(reason: str | None) -> str:
    """A quarantine reason as the public page may show it. The loaders write most as plain
    phrases ("4/A row could not be paired with its original"); a parser's message is technical."""
    line = last_line(reason)
    if not line:
        return "no reason recorded"
    if line.startswith("parse error"):
        return "the document couldn't be read"
    if looks_technical(line) or error_class(line):
        return "it failed a check while loading"
    return line[:300]


def plain_alert_error(error: str | None) -> str | None:
    """Why an alert couldn't be delivered, for the public page (see ALERT_ERROR_PUBLIC)."""
    if not error:
        return None
    return error if error == _ALERT_NOT_ACCEPTED else ALERT_ERROR_PUBLIC


def _fetch_sentence(url: str, tries: int, why: str) -> str:
    """'<what> didn't answer (HTTP 503) after 5 tries.' or '<what> couldn't be reached ...'.

    For one of a filing's own files the subject is EDGAR, and the filing is named after
    it: "EDGAR didn't answer for filing 0001193125-26-396718 (HTTP 503) after 5 tries."
    """
    accession = _filing_accession(url)
    if accession:
        what, about = "EDGAR", f" for filing {accession}"
    else:
        what, about = _what_was_fetched(url), ""
    status = _HTTP_STATUS.match(why)
    if status:
        return f"{what} didn't answer{about} (HTTP {status['status']}) after {tries} tries."
    # The last try ended without any answer: "ConnectError: [Errno 11001] getaddrinfo failed".
    inner = _EXCEPTION_LINE.match(why)
    problem = _connection_problem(inner["cls"], inner["msg"] or "") if inner else _connection_problem("", why)
    return f"{what} couldn't be reached{about} after {tries} tries ({problem})."


def _filing_accession(url: str) -> str | None:
    """The accession number when a URL points into one filing's folder on EDGAR
    (.../Archives/edgar/data/<cik>/<18 digits>/...), written the usual way: 0001193125-26-396718."""
    filing = re.search(r"/Archives/edgar/data/\d+/(\d{10})(\d{2})(\d{6})", urlsplit(url).path)
    return f"{filing[1]}-{filing[2]}-{filing[3]}" if filing else None


def _connection_problem(cls: str, message: str) -> str:
    if "getaddrinfo" in message or "Name or service not known" in message:
        return "the address lookup failed, which usually means the internet connection was down"
    if "Timeout" in cls:
        return "no answer in time"
    if cls == "ConnectError":
        return "the connection couldn't be made"
    return "the connection dropped"


def _what_was_fetched(url: str) -> str:
    """Name the document a URL points at, in words ("The SEC's daily index for Sep 24")."""
    parts = urlsplit(url)
    path = parts.path
    daily = re.search(r"/daily-index/.*?(\d{4})(\d{2})(\d{2})\.idx$", path)
    if daily:
        day = date(int(daily[1]), int(daily[2]), int(daily[3]))
        return f"The SEC's daily index for {day:%b} {day.day}"
    if path.endswith("browse-edgar"):
        form = parse_qs(parts.query).get("type", [""])[0]
        return f"EDGAR's list of the latest Form {form} filings" if form else "EDGAR's list of the latest filings"
    if "company_tickers" in path:
        return "The SEC's ticker list"
    if "/submissions/" in path:
        return "An SEC company profile"
    if "structureddata" in path or "insider-transactions-data-sets" in path:
        return "The SEC's quarterly data set"
    host = parts.hostname or ""
    if host == "sec.gov" or host.endswith(".sec.gov"):
        return "An SEC page"
    return "An outside data source"


def _sentence(line: str) -> str:
    """Capitalise the first letter and end with a full stop."""
    text = line.strip()
    text = text[:1].upper() + text[1:]
    return text if text.endswith((".", "!", "?")) else f"{text}."
