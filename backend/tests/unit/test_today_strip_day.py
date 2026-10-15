"""Which day the Live page's today strip covers (smt.api.routers.feed.strip_day).

EDGAR takes no filings on weekends and federal holidays, so on those days the strip
covers the latest SEC business day instead of an always-empty "today".
"""

from __future__ import annotations

from datetime import date

from smt.api.routers.feed import strip_day


def test_a_business_day_is_itself() -> None:
    assert strip_day(date(2026, 9, 25)) == date(2026, 9, 25)  # a Friday


def test_a_weekend_goes_back_to_friday() -> None:
    assert strip_day(date(2026, 9, 26)) == date(2026, 9, 25)  # Saturday
    assert strip_day(date(2026, 9, 27)) == date(2026, 9, 25)  # Sunday


def test_a_holiday_goes_back_past_the_weekend() -> None:
    # Labor Day, Monday Sep 7 2026: the latest business day is Friday Sep 4.
    assert strip_day(date(2026, 9, 7)) == date(2026, 9, 4)
