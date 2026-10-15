"""Small helpers that turn times and durations into plain words for alerts."""

from __future__ import annotations

from datetime import datetime, timedelta

from smt.domain.business_days import to_eastern


def when_et(moment: datetime) -> str:
    """'Sep 24, 11:02 AM ET'. Written by hand because %-I doesn't work on Windows."""
    local = to_eastern(moment)
    hour = local.hour % 12 or 12
    half = "AM" if local.hour < 12 else "PM"
    return f"{local:%b} {local.day}, {hour}:{local:%M} {half} ET"


def duration_text(span: timedelta) -> str:
    """'45 seconds', '23 minutes', '2 hours 5 minutes', '3 days 4 hours'."""
    seconds = max(0, int(span.total_seconds()))
    if seconds < 60:
        return _plural(seconds, "second")
    minutes = seconds // 60
    if minutes < 60:
        return _plural(minutes, "minute")
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return _plural(hours, "hour") + (f" {_plural(minutes, 'minute')}" if minutes else "")
    days, hours = divmod(hours, 24)
    return _plural(days, "day") + (f" {_plural(hours, 'hour')}" if hours else "")


def count_text(n: int, singular: str, plural: str | None = None) -> str:
    """'1 filing', '1,240 filings'."""
    word = singular if n == 1 else (plural or f"{singular}s")
    return f"{n:,} {word}"


def _plural(n: int, unit: str) -> str:
    return f"{n} {unit}" if n == 1 else f"{n} {unit}s"
