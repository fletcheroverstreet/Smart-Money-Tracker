"""Which filing days the site holds completely (EDGAR Forms 3/4/5 and 144).

A day is checked against the SEC's daily index, the official list of every
filing accepted that day. The nightly reconciliation checks the last few days
and the gap filler checks older ones. Each check is stored in
reconciliation_results with the accessions that were missing at the time.
Missing filings are queued, so a day's share rises as they load; "found" is
therefore recomputed from what is held now, not taken from the check.

* **loaded:** at least 99% of the day's indexed filings are held. A handful
  can fail for good (a filing with no readable XML) without hiding a day.
* **partial:** at least half, but under 99%, are held. This is usually a day
  the gap filler is still loading.
* **missing:** under half are held.

Days never checked are judged by their Form 4 count. The quarterly data sets
cover January to June, and today's index isn't published until about 22:00
ET. EDGAR receives hundreds of Form 4s every business day (the fewest on any
2026 day in the data sets is 293), so a day with fewer than 50 hasn't been
loaded. Days the Form 3 feed paged back into are an example: a few dozen
Form 3s and no Form 4s. GET /api/meta/coverage and the gap filler both use
this module, so the site's "not loaded yet" notes and the filler agree.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from smt.domain.business_days import is_sec_business_day

#: reconciliation_results.source for the EDGAR Forms 3/4/5/144 check.
SOURCE = "edgar_ownership_144"
#: Share of a day's indexed filings that must be held for the day to count as loaded.
LOADED_SHARE = 0.99
#: Below this share a day counts as not loaded at all; between the two, partly loaded.
PARTIAL_SHARE = 0.5
#: A never-checked business day with fewer Form 4s than this isn't loaded.
MIN_FORM4_UNCHECKED = 50

State = Literal["loaded", "partial", "missing"]


@dataclass(frozen=True)
class DayCoverage:
    day: date
    #: Filings in the day's index, and how many of them are held now (None: never checked).
    expected: int | None
    found: int | None
    #: Forms 4 and 4/A held with this filing date.
    form4: int
    state: State


@dataclass(frozen=True)
class Span:
    """A run of consecutive business days in the same state (weekends and holidays don't break it)."""

    first: date
    last: date
    business_days: int
    state: State


def _state(expected: int | None, found: int | None, form4: int) -> State:
    if expected is None or found is None:
        return "loaded" if form4 >= MIN_FORM4_UNCHECKED else "missing"
    if expected == 0 or found >= expected * LOADED_SHARE:
        return "loaded"
    return "partial" if found >= expected * PARTIAL_SHARE else "missing"


async def checked_days(session: AsyncSession) -> dict[date, tuple[int, int]]:
    """{day: (expected, found now)} for every day checked against its daily index."""
    rows = (await session.execute(text("""
        SELECT r.day, r.expected,
               -- A filing EDGAR removed after accepting it (queue status 'gone') can't be held,
               -- so it doesn't count against the day.
               r.expected - (SELECT count(*) FROM jsonb_array_elements_text(r.missing) AS m(accession)
                              WHERE NOT EXISTS (SELECT 1 FROM filings f
                                                 WHERE f.source = 'edgar' AND f.source_key = m.accession)
                                AND NOT EXISTS (SELECT 1 FROM edgar_queue q
                                                 WHERE q.accession = m.accession AND q.status = 'gone')) AS found
          FROM reconciliation_results r
         WHERE r.source = :source
    """), {"source": SOURCE})).all()
    return {r.day: (int(r.expected), int(r.found)) for r in rows}


async def day_coverage(session: AsyncSession, today: date) -> list[DayCoverage]:
    """Every SEC business day from the first held Form 3/4/5 filing date to yesterday, oldest first.

    Today is left out: its index isn't published until the evening, and the
    Live feed shows what has arrived so far.
    """
    first = (await session.execute(text(
        "SELECT min(filed_on) FROM filings WHERE source = 'edgar' "
        "AND form_type IN ('3', '3/A', '4', '4/A', '5', '5/A')"))).scalar_one()
    if first is None:
        return []
    form4 = {r[0]: int(r[1]) for r in (await session.execute(text(
        "SELECT filed_on, count(*) FROM filings WHERE source = 'edgar' AND form_type IN ('4', '4/A') "
        "AND filed_on >= :first GROUP BY 1"), {"first": first})).all()}
    checked = await checked_days(session)
    out: list[DayCoverage] = []
    day = first
    while day < today:
        if is_sec_business_day(day):
            expected, found = checked.get(day, (None, None))
            n4 = form4.get(day, 0)
            out.append(DayCoverage(day, expected, found, n4, _state(expected, found, n4)))
        day += timedelta(days=1)
    return out


def not_loaded_spans(days: list[DayCoverage]) -> list[Span]:
    """Runs of business days that aren't fully loaded, split where the state changes.
    `days` is day_coverage's list: every business day, so the previous entry is the previous business day."""
    spans: list[Span] = []
    prev: DayCoverage | None = None
    for d in days:
        if d.state != "loaded":
            if prev is not None and prev.state == d.state:
                s = spans[-1]
                spans[-1] = Span(s.first, d.day, s.business_days + 1, d.state)
            else:
                spans.append(Span(d.day, d.day, 1, d.state))
        prev = d
    return spans
