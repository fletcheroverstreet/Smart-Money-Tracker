"""Calendars and deadlines. Every expected value below was worked out by hand.

2026 reference points:
    Columbus Day      Mon 2026-10-12   federal holiday, NYSE open
    Veterans Day      Wed 2026-11-11   federal holiday, NYSE open
    Good Friday       Fri 2026-04-03   NYSE closed, federal business day
    Independence Day  Sat 2026-07-04   observed Fri 2026-07-03 (both calendars)
    Thanksgiving      Thu 2026-11-26   day after (Fri 27th) closes 13:00
    Presidents' Day   Mon 2026-02-16   federal holiday and NYSE holiday
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from smt.domain import business_days as bd

EDT = timezone(timedelta(hours=-4))
EST = timezone(timedelta(hours=-5))


# ---------------------------------------------------------------- SEC calendar
@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 10, 12), False),  # Columbus Day
        (date(2026, 11, 11), False),  # Veterans Day
        (date(2026, 4, 3), True),     # Good Friday is a federal business day
        (date(2026, 7, 3), False),    # July 4th observed
        (date(2026, 9, 26), False),   # Saturday
        (date(2025, 1, 9), False),    # National Day of Mourning (config/calendars.yaml)
        (date(2026, 9, 24), True),    # ordinary Thursday
    ],
)
def test_sec_business_days(day: date, expected: bool) -> None:
    assert bd.is_sec_business_day(day) is expected


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 10, 12), True),   # Columbus Day: market open
        (date(2026, 11, 11), True),   # Veterans Day: market open
        (date(2026, 4, 3), False),    # Good Friday: market closed
        (date(2026, 7, 3), False),    # Independence Day observed
        (date(2026, 11, 26), False),  # Thanksgiving
    ],
)
def test_nyse_trading_days(day: date, expected: bool) -> None:
    assert bd.is_trading_day(day) is expected


def test_business_days_between_skips_holidays() -> None:
    # Fri Oct 9 -> Wed Oct 14 2026: Mon 12 is Columbus Day, so only Tue and Wed count.
    assert bd.sec_business_days_between(date(2026, 10, 9), date(2026, 10, 14)) == 2
    assert bd.sec_business_days_between(date(2026, 10, 14), date(2026, 10, 9)) == -2
    assert bd.sec_business_days_between(date(2026, 10, 9), date(2026, 10, 9)) == 0


# ---------------------------------------------------------------- filing dates (Reg S-T 232.13)
def test_form4_accepted_at_2131_et_is_same_day() -> None:
    # Real case: BRK Form 4 accepted 2026-09-22T01:31:10Z = 21:31 EDT on 9/21, filingDate 2026-09-21.
    accepted = datetime(2026, 9, 22, 1, 31, 10, tzinfo=UTC)
    assert bd.filing_date_for(accepted, "4") == date(2026, 9, 21)


def test_11k_accepted_after_1730_rolls_to_next_business_day() -> None:
    # Real case: BRK 11-K accepted 2026-06-15T21:52:14Z = 17:52 EDT, filingDate 2026-06-16.
    accepted = datetime(2026, 6, 15, 21, 52, 14, tzinfo=UTC)
    assert bd.filing_date_for(accepted, "11-K") == date(2026, 6, 16)


def test_schedule13g_accepted_2018_et_is_same_day() -> None:
    # Real case: SCHEDULE 13G accepted 2026-08-15T00:18:26Z = 20:18 EDT on 8/14, filed 8/14.
    accepted = datetime(2026, 8, 15, 0, 18, 26, tzinfo=UTC)
    assert bd.filing_date_for(accepted, "SCHEDULE 13G") == date(2026, 8, 14)


def test_13f_after_1730_on_friday_moves_to_monday() -> None:
    accepted = datetime(2026, 8, 14, 17, 45, tzinfo=EDT)
    assert bd.filing_date_for(accepted, "13F-HR") == date(2026, 8, 17)


def test_form4_at_2200_exactly_is_next_business_day() -> None:
    accepted = datetime(2026, 9, 24, 22, 0, tzinfo=EDT)
    assert bd.filing_date_for(accepted, "4") == date(2026, 9, 25)


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(ValueError, match="naive"):
        bd.filing_date_for(datetime(2026, 9, 24, 12, 0), "4")


# ---------------------------------------------------------------- deadlines
def test_form4_deadline_skips_columbus_day() -> None:
    # Trade Fri Oct 9: Mon 12 is a holiday, so business day 1 = Tue 13, 2 = Wed 14.
    assert bd.form4_deadline(date(2026, 10, 9)) == date(2026, 10, 14)


def test_form4_deadline_counts_good_friday_as_business_day() -> None:
    # Trade Thu Apr 2: Fri Apr 3 (Good Friday) is a federal business day = day 1; Mon Apr 6 = day 2.
    assert bd.form4_deadline(date(2026, 4, 2)) == date(2026, 4, 6)


def test_form4_deadline_uses_deemed_execution_date() -> None:
    # Trade Tue 9/1, broker notice Thu 9/10 is past the cap: the deemed date is the 3rd business
    # day after the trade (Fri 9/4); +2 business days skips Labor Day (Mon 9/7) -> Wed 9/9.
    assert bd.form4_deadline(date(2026, 9, 1), deemed_execution_on=date(2026, 9, 10)) == date(2026, 9, 9)
    # A notice within the cap moves the clock: Thu 9/3 -> Tue 9/8.
    assert bd.form4_deadline(date(2026, 9, 1), deemed_execution_on=date(2026, 9, 3)) == date(2026, 9, 8)


def test_13f_deadline_rolls_past_weekend_and_presidents_day() -> None:
    # 2025-12-31 + 45 days = Sat 2026-02-14; Mon 2/16 is Presidents' Day -> Tue 2/17.
    assert bd.form13f_deadline(date(2025, 12, 31)) == date(2026, 2, 17)
    # 2026-06-30 + 45 = Fri 2026-08-14 (a business day).
    assert bd.form13f_deadline(date(2026, 6, 30)) == date(2026, 8, 14)


def test_13d_initial_deadline_is_five_business_days() -> None:
    # Event Wed Nov 4 2026: Thu 5, Fri 6, Mon 9, Tue 10, (Wed 11 Veterans Day skipped), Thu 12.
    assert bd.schedule13d_initial_deadline(date(2026, 11, 4)) == date(2026, 11, 12)


def test_ptr_deadline_45_day_limb() -> None:
    # Trade Jul 15, notified Aug 1: 45 days -> Aug 29; 30 after notice -> Aug 31. Earlier wins.
    assert bd.ptr_deadline(date(2026, 7, 15), date(2026, 8, 1)) == (date(2026, 8, 29), "45_days_after_trade")


def test_ptr_deadline_notification_limb() -> None:
    # Trade Jul 1, notified Jul 5: 45 days -> Aug 15; 30 after notice -> Aug 4.
    assert bd.ptr_deadline(date(2026, 7, 1), date(2026, 7, 5)) == (date(2026, 8, 4), "30_days_after_notification")


def test_ptr_deadline_without_notification_date() -> None:
    assert bd.ptr_deadline(date(2026, 8, 2), None) == (date(2026, 9, 16), "45_days_after_trade")


# ---------------------------------------------------------------- NYSE sessions
def test_early_closes() -> None:
    assert bd.is_early_close(date(2026, 11, 27))       # day after Thanksgiving
    assert bd.is_early_close(date(2026, 12, 24))       # Christmas Eve (Thursday)
    assert not bd.is_early_close(date(2026, 7, 3))     # holiday itself in 2026, not a session
    assert bd.is_early_close(date(2025, 7, 3))         # Thursday before July 4, 2025
    assert not bd.is_early_close(date(2026, 9, 24))


def test_first_close_after_before_and_after_the_bell() -> None:
    before = datetime(2026, 9, 24, 15, 59, tzinfo=EDT)
    after = datetime(2026, 9, 24, 16, 0, tzinfo=EDT)
    assert bd.first_close_after(before) == datetime(2026, 9, 24, 16, 0, tzinfo=bd.ET)
    assert bd.first_close_after(after) == datetime(2026, 9, 25, 16, 0, tzinfo=bd.ET)


def test_first_close_after_on_a_half_day() -> None:
    # 13:30 on the day after Thanksgiving is after that day's 13:00 close -> Monday Nov 30.
    moment = datetime(2026, 11, 27, 13, 30, tzinfo=EST)
    assert bd.first_close_after(moment) == datetime(2026, 11, 30, 16, 0, tzinfo=bd.ET)


def test_first_close_after_skips_good_friday() -> None:
    moment = datetime(2026, 4, 2, 18, 0, tzinfo=EDT)  # Thursday evening
    assert bd.first_close_after(moment) == datetime(2026, 4, 6, 16, 0, tzinfo=bd.ET)


def test_date_only_disclosure_counts_from_next_trading_day() -> None:
    # A row known only by date 2026-09-24 enters at Friday's close, never Thursday's.
    assert bd.first_close_after_date_only(date(2026, 9, 24)) == datetime(2026, 9, 25, 16, 0, tzinfo=bd.ET)


def test_form4_deadline_with_a_deemed_execution_date() -> None:
    # Rule 16a-3(g)(2): the clock starts at the deemed date (the broker's notice)...
    trade = date(2026, 9, 14)  # Monday
    assert bd.form4_deadline(trade, date(2026, 9, 15)) == date(2026, 9, 17)
    # ...but a deemed date can be no later than the 3rd business day after the trade (Thu 9/17).
    assert bd.form4_deadline(trade, date(2026, 9, 25)) == bd.form4_deadline(trade, date(2026, 9, 17))
    assert bd.form4_deadline(trade, date(2026, 9, 25)) == date(2026, 9, 21)
    # A deemed date before the trade is a typo; the trade date is used.
    assert bd.form4_deadline(trade, date(2026, 9, 1)) == bd.form4_deadline(trade)
