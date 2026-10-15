"""Calendars, filing dates and legal deadlines.

Two calendars are used, and mixing them up produces wrong late flags or
shifted return windows (PLAN.md §8.1, DEC-007):

* **SEC business days** (for filing dates and deadlines): every day except
  Saturdays, Sundays, federal holidays and one-off closures such as national
  days of mourning (17 CFR 240.0-3; Rule 13d-1 defines a business day the
  same way). Federal holidays include Columbus Day and Veterans Day, when the
  stock market is open.
* **NYSE trading days** (for returns): the exchange calendar. Good Friday is
  a market holiday but a normal federal business day. Some sessions close
  early at 13:00 ET.

All clock times here are US Eastern, because EDGAR and the exchanges run on
Eastern time. Inputs with a timezone are converted; naive datetimes are
rejected so a UTC-vs-ET mix-up can never pass silently.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import holidays

from smt.domain.calendar_config import sec_extra_closures

ET = ZoneInfo("America/New_York")

#: Forms whose acceptance until 22:00 ET still counts as filed that business
#: day (Reg S-T 232.13(a)(4)). Every other form cuts off at 17:30 ET (232.13(a)(2)).
LATE_CUTOFF_FORMS = frozenset({
    "3", "3/A", "4", "4/A", "5", "5/A", "144", "144/A",
    "SCHEDULE 13D", "SCHEDULE 13D/A", "SCHEDULE 13G", "SCHEDULE 13G/A",
    "SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A", "SC 14N", "SC 14N/A",
})
LATE_CUTOFF = time(22, 0)
STANDARD_CUTOFF = time(17, 30)

NYSE_CLOSE = time(16, 0)
NYSE_EARLY_CLOSE = time(13, 0)


# ---------------------------------------------------------------- SEC calendar

@lru_cache(maxsize=64)
def _federal_holidays(year: int) -> frozenset[date]:
    """US federal holidays for a year, including weekend-observed dates."""
    return frozenset(holidays.country_holidays("US", years=year, observed=True).keys())


def is_sec_business_day(day: date) -> bool:
    """True if the SEC is open: not a weekend, federal holiday or special closure."""
    if day.weekday() >= 5:
        return False
    if day in _federal_holidays(day.year):
        return False
    return day not in sec_extra_closures()


def next_sec_business_day(day: date) -> date:
    """The first SEC business day strictly after `day`."""
    candidate = day + timedelta(days=1)
    while not is_sec_business_day(candidate):
        candidate += timedelta(days=1)
    return candidate


def roll_to_sec_business_day(day: date) -> date:
    """`day` itself if it is an SEC business day, otherwise the next one.

    Rule 0-3: a deadline falling on a weekend or holiday moves to the next
    business day.
    """
    return day if is_sec_business_day(day) else next_sec_business_day(day)


def add_sec_business_days(day: date, n: int) -> date:
    """The n-th SEC business day after `day` (n >= 0; `day` itself is not counted)."""
    if n < 0:
        raise ValueError("n must be >= 0")
    result = day
    for _ in range(n):
        result = next_sec_business_day(result)
    return result


def sec_business_days_between(start: date, end: date) -> int:
    """Number of SEC business days in (start, end]. Negative if end < start."""
    if end == start:
        return 0
    sign = 1 if end > start else -1
    lo, hi = (start, end) if sign > 0 else (end, start)
    count = 0
    cursor = lo
    while cursor < hi:
        cursor += timedelta(days=1)
        if is_sec_business_day(cursor):
            count += 1
    return sign * count


# ---------------------------------------------------------------- filing dates

def to_eastern(moment: datetime) -> datetime:
    """Convert an aware datetime to US Eastern. Naive datetimes are an error."""
    if moment.tzinfo is None:
        raise ValueError("naive datetime: pass an aware datetime (UTC or with offset)")
    return moment.astimezone(ET)


def filing_date_for(accepted_at: datetime, form_type: str) -> date:
    """The legal filing date EDGAR assigns to a submission accepted at `accepted_at`.

    Reg S-T Rule 13 (17 CFR 232.13): a submission made on a business day
    before the cutoff gets that day's date; anything later, or on a
    non-business day, is dated the next business day. The cutoff is 22:00 ET
    for Forms 3/4/5, 144 and Schedules 13D/13G, and 17:30 ET for everything
    else (including 13F).
    """
    local = to_eastern(accepted_at)
    cutoff = LATE_CUTOFF if form_type.upper() in LATE_CUTOFF_FORMS else STANDARD_CUTOFF
    day = local.date()
    if is_sec_business_day(day) and local.time() < cutoff:
        return day
    return next_sec_business_day(day)


# ---------------------------------------------------------------- deadlines

def form4_deadline(executed_on: date, deemed_execution_on: date | None = None) -> date:
    """Form 4 due date: before the end of the 2nd business day after execution.

    Rule 16a-3(g)(1). When Rule 16a-3(g)(2)/(3) applies (10b5-1 plans and
    discretionary transactions where the insider doesn't pick the date), the
    date the broker notifies the insider is the *deemed* execution date and
    the clock starts there, but a deemed date can be no later than the third
    business day after the trade. The deemed date itself comes from the filer
    and can't be checked, so the loader still labels such flags approximate.
    """
    start = executed_on
    if deemed_execution_on is not None:
        start = min(max(deemed_execution_on, executed_on), add_sec_business_days(executed_on, 3))
    return add_sec_business_days(start, 2)


def schedule13d_initial_deadline(event_on: date) -> date:
    """Initial Schedule 13D: within 5 business days of crossing 5% (2023 amendments)."""
    return add_sec_business_days(event_on, 5)


def form13f_deadline(period_end: date) -> date:
    """13F due date: 45 calendar days after quarter end, rolled past weekends/holidays."""
    return roll_to_sec_business_day(period_end + timedelta(days=45))


def ptr_deadline(traded_on: date, notified_on: date | None) -> tuple[date, str]:
    """Congressional PTR deadline and which legal limb produced it.

    The STOCK Act requires a periodic transaction report within 30 days of
    being notified of a transaction, and in no case later than 45 days after
    the transaction. The deadline is the earlier of the two; without a
    notification date only the 45-day limb can be applied.

    Whether a deadline on a weekend rolls to the next business day is not yet
    confirmed from House/Senate Ethics guidance (PLAN.md §8.1), so no rolling
    is applied here. Phase 2 settles it.

    Returns (deadline, limb) with limb in {"45_days_after_trade",
    "30_days_after_notification"}.
    """
    by_trade = traded_on + timedelta(days=45)
    if notified_on is None:
        return by_trade, "45_days_after_trade"
    by_notice = notified_on + timedelta(days=30)
    if by_notice < by_trade:
        return by_notice, "30_days_after_notification"
    return by_trade, "45_days_after_trade"


# ---------------------------------------------------------------- NYSE calendar

@lru_cache(maxsize=64)
def _nyse_holidays(year: int) -> frozenset[date]:
    return frozenset(holidays.financial_holidays("NYSE", years=year).keys())


def is_trading_day(day: date) -> bool:
    """True if the NYSE holds a session on `day`."""
    return day.weekday() < 5 and day not in _nyse_holidays(day.year)


def next_trading_day(day: date) -> date:
    """The first trading day strictly after `day`."""
    candidate = day + timedelta(days=1)
    while not is_trading_day(candidate):
        candidate += timedelta(days=1)
    return candidate


def roll_to_trading_day(day: date) -> date:
    """`day` if it is a trading day, otherwise the next one."""
    return day if is_trading_day(day) else next_trading_day(day)


def is_early_close(day: date) -> bool:
    """True if the NYSE closes at 13:00 ET on `day`.

    NYSE's standing rules: the day after Thanksgiving; Christmas Eve when it
    is a trading day; and July 3 when it is a trading day (the day before
    Independence Day). Rare exceptions can be added to the calendar config.
    """
    if not is_trading_day(day):
        return False
    thanksgiving = _thanksgiving(day.year)
    if day == thanksgiving + timedelta(days=1):
        return True
    if (day.month, day.day) == (12, 24):
        return True
    return (day.month, day.day) == (7, 3)


def session_close(day: date) -> datetime:
    """The NYSE close on a trading day, as an aware Eastern datetime."""
    if not is_trading_day(day):
        raise ValueError(f"{day} is not an NYSE trading day")
    close = NYSE_EARLY_CLOSE if is_early_close(day) else NYSE_CLOSE
    return datetime.combine(day, close, tzinfo=ET)


def first_close_after(moment: datetime) -> datetime:
    """The first NYSE close strictly after `moment`.

    This is the entry point for the "follower" return (PLAN.md §8.2): a
    filing accepted before the close counts from that day's close; one
    accepted at or after the close (13:00 on half days) counts from the next
    trading day's close.
    """
    local = to_eastern(moment)
    day = local.date()
    if is_trading_day(day) and local < session_close(day):
        return session_close(day)
    return session_close(next_trading_day(day))


def first_close_after_date_only(day: date) -> datetime:
    """Entry for rows whose disclosure has only a date: the NEXT trading day's close.

    Without a time we cannot know whether the public saw it before the close,
    so we assume it did not. This avoids look-ahead bias (PLAN.md §8.2, DEC-008).
    """
    return session_close(next_trading_day(day))


def _thanksgiving(year: int) -> date:
    """Fourth Thursday of November."""
    first = date(year, 11, 1)
    offset = (3 - first.weekday()) % 7  # Thursday is weekday 3
    return first + timedelta(days=offset + 21)
