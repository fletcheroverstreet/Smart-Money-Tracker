"""When jobs run.

A schedule answers one question: given that a job last started at `now`, when
should it run next? Schedules are pure functions of time, so they're easy to
test.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta

from smt.domain.business_days import ET, is_sec_business_day, is_trading_day, to_eastern


class Schedule:
    """Base class. Subclasses implement `next_after`."""

    def next_after(self, now: datetime) -> datetime:  # pragma: no cover - interface
        raise NotImplementedError

    def describe(self) -> str:  # pragma: no cover - interface
        raise NotImplementedError


@dataclass(frozen=True)
class Every(Schedule):
    """Run every `seconds`."""

    seconds: float

    def next_after(self, now: datetime) -> datetime:
        return now + timedelta(seconds=self.seconds)

    def describe(self) -> str:
        return f"every {self.seconds:g} s"


@dataclass(frozen=True)
class EdgarHours(Schedule):
    """Fast inside EDGAR's filing window, slow outside it.

    EDGAR accepts filings 06:00-22:00 ET on business days (spec §2.1). Inside
    that window we poll every `active_seconds`; otherwise every `idle_seconds`,
    which still catches the odd late acceptance.
    """

    active_seconds: float = 60
    idle_seconds: float = 600
    opens: time = time(6, 0)
    closes: time = time(22, 0)

    def is_active(self, moment: datetime) -> bool:
        local = to_eastern(moment)
        return is_sec_business_day(local.date()) and self.opens <= local.time() < self.closes

    def next_after(self, now: datetime) -> datetime:
        if self.is_active(now):
            return now + timedelta(seconds=self.active_seconds)
        # Outside the window, poll slowly, but never sleep past the moment it opens: a Form 4
        # accepted at 06:01 once waited for a 10-minute idle step that began at 05:56.
        idle = now + timedelta(seconds=self.idle_seconds)
        opening = self._next_opening(now)
        return min(idle, opening) if opening > now else idle

    def _next_opening(self, now: datetime) -> datetime:
        """The next time the window opens (today's or a later business day's `opens`)."""
        local = to_eastern(now)
        day = local.date()
        for _ in range(10):
            if is_sec_business_day(day):
                candidate = datetime.combine(day, self.opens, tzinfo=local.tzinfo)
                if candidate > local:
                    return candidate
            day += timedelta(days=1)
        return now + timedelta(days=10)

    def describe(self) -> str:
        return (f"every {self.active_seconds:g} s from {self.opens:%H:%M} to {self.closes:%H:%M} ET "
                f"on business days, else every {self.idle_seconds:g} s")


@dataclass(frozen=True)
class DailyAt(Schedule):
    """Once a day at a fixed Eastern time, optionally only on business/trading days."""

    at: time
    days: str = "all"  # "all" | "sec_business" | "trading"

    def _ok(self, local: datetime) -> bool:
        if self.days == "sec_business":
            return is_sec_business_day(local.date())
        if self.days == "trading":
            return is_trading_day(local.date())
        return True

    def next_after(self, now: datetime) -> datetime:
        local = to_eastern(now)
        candidate = datetime.combine(local.date(), self.at, tzinfo=ET)
        if candidate <= local:
            candidate += timedelta(days=1)
        while not self._ok(candidate):
            candidate += timedelta(days=1)
        return candidate

    def describe(self) -> str:
        suffix = {"all": "daily", "sec_business": "on SEC business days", "trading": "on trading days"}
        return f"{self.at:%H:%M} ET {suffix[self.days]}"
