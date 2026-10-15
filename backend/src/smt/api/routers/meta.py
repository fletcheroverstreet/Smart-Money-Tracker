"""Reference content for the Methodology and Sources pages, served from the same code
that applies it, so the explanations can't drift from the logic."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel
from sqlalchemy import text

from smt.api.deps import Session
from smt.domain.business_days import ET
from smt.domain.codes import CODE_LABELS, OPEN_MARKET_CODES
from smt.sources.edgar.coverage import day_coverage, not_loaded_spans

router = APIRouter(prefix="/api/meta", tags=["meta"])


class CodeOut(BaseModel):
    code: str
    label: str
    open_market: bool


class SourceOut(BaseModel):
    key: str
    name: str
    reveals: str
    legal_lag: str
    our_cadence: str
    url: str
    phase: int
    status: str


SOURCES: list[dict[str, Any]] = [
    {"key": "edgar_form4", "name": "SEC Forms 3, 4 and 5 (EDGAR)",
     "reveals": "Trades and holdings of company officers, directors and 10% owners, including funds.",
     "legal_lag": "Form 4: by the end of the 2nd business day after the trade. Form 3: within 10 days of "
                  "becoming an insider. Form 5: within 45 days after the fiscal year ends.",
     "our_cadence": "Form 4 polled every 20 seconds and Forms 3 and 5 every 60 seconds from 06:00 to "
                    "22:00 ET on business days, every 10 minutes otherwise; reconciled nightly against the "
                    "EDGAR daily index, and any day not fully loaded is checked against it every 10 minutes.",
     "url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4", "phase": 1, "status": "live"},
    {"key": "edgar_form144", "name": "SEC Form 144 (EDGAR)",
     "reveals": "Notices that an affiliate intends to sell restricted or control securities.",
     "legal_lag": "Filed when the sale order is placed.",
     "our_cadence": "Polled every 60 seconds in filing hours (every 10 minutes otherwise), checked "
                    "nightly against the SEC's daily index, and any day not fully loaded every 10 minutes.",
     "url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=144", "phase": 1, "status": "live"},
    {"key": "sec_tickers", "name": "SEC company tickers",
     "reveals": "Which ticker belongs to which company (current tickers only).",
     "legal_lag": "Updated by the SEC on business days.", "our_cadence": "Daily.",
     "url": "https://www.sec.gov/files/company_tickers_exchange.json", "phase": 1, "status": "live"},
    {"key": "house_ptr", "name": "House periodic transaction reports",
     "reveals": "Trades by members of the House, their spouses and dependent children.",
     "legal_lag": "Within 30 days of notification and no later than 45 days after the trade.",
     "our_cadence": "Hourly (coming in Phase 2).",
     "url": "https://disclosures-clerk.house.gov/FinancialDisclosure", "phase": 2, "status": "planned"},
    {"key": "senate_ptr", "name": "Senate periodic transaction reports",
     "reveals": "Trades by senators, their spouses and dependent children.",
     "legal_lag": "Within 30 days of notification and no later than 45 days after the trade.",
     "our_cadence": "Every 30 minutes (coming in Phase 2).",
     "url": "https://efdsearch.senate.gov/search/", "phase": 2, "status": "planned"},
    {"key": "edgar_13f", "name": "SEC Form 13F",
     "reveals": "Quarter-end long positions of managers with over $100 million in 13(f) securities.",
     "legal_lag": "Within 45 days after quarter end. A snapshot, not trades.",
     "our_cadence": "Coming in Phase 3.", "url": "https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets",
     "phase": 3, "status": "planned"},
    {"key": "edgar_13dg", "name": "SEC Schedules 13D and 13G",
     "reveals": "Stakes above 5%, and for 13D the holder's stated purpose.",
     "legal_lag": "13D: 5 business days; amendments 2 business days. 13G: depends on the filer type.",
     "our_cadence": "Coming in Phase 3.", "url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=SCHEDULE%2013D",
     "phase": 3, "status": "planned"},
]


@router.get("/codes")
async def codes(response: Response) -> list[CodeOut]:
    """Form 4 transaction codes and their plain-English meanings (for tooltips)."""
    response.headers["Cache-Control"] = "public, max-age=86400"
    return [CodeOut(code=c, label=label, open_market=c in OPEN_MARKET_CODES) for c, label in CODE_LABELS.items()]


@router.get("/sources")
async def sources(response: Response) -> list[SourceOut]:
    """Every data source, what it reveals, its legal lag and how often we check it."""
    response.headers["Cache-Control"] = "public, max-age=3600"
    return [SourceOut(**s) for s in SOURCES]


class DateSpan(BaseModel):
    first: date
    last: date
    business_days: int
    #: "missing": under half of those days' filings are held (or the days were never checked and
    #: hold almost no Form 4s). "partial": at least half but under 99% (usually still loading).
    state: Literal["missing", "partial"] = "missing"


class DataCoverage(BaseModel):
    """Which filing dates this site holds for Forms 3/4/5 (so windows like "last 90 days" are honest)."""

    first_filed: date | None
    last_filed: date | None
    #: Runs of SEC business days that aren't fully loaded, oldest first (smt.sources.edgar.coverage).
    gaps: list[DateSpan]


def _today() -> date:
    """Today's date in New York (a function so tests can fix it)."""
    return datetime.now(ET).date()


@router.get("/coverage")
async def coverage(session: Session, response: Response) -> DataCoverage:
    """Filing dates held for Forms 3/4/5, and the business days inside that range that aren't
    fully loaded.

    Each day is compared with the SEC's daily index (the official list of the
    day's filings): a day counts as loaded when 99% of its filings are held.
    Days not yet compared count as loaded when they hold at least 50 Form 4s
    (EDGAR receives hundreds every business day). Today is left out until its
    index is published in the evening.
    """
    response.headers["Cache-Control"] = "public, max-age=300"
    bounds = (await session.execute(text(
        "SELECT min(filed_on), max(filed_on) FROM filings WHERE source = 'edgar' "
        "AND form_type IN ('3', '3/A', '4', '4/A', '5', '5/A')"))).one()
    if bounds[0] is None:
        return DataCoverage(first_filed=None, last_filed=None, gaps=[])
    days = await day_coverage(session, _today())
    gaps = [DateSpan(first=s.first, last=s.last, business_days=s.business_days, state=s.state)
            for s in not_loaded_spans(days) if s.state != "loaded"]
    return DataCoverage(first_filed=bounds[0], last_filed=bounds[1], gaps=gaps)
