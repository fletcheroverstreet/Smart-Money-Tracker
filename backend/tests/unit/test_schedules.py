"""EdgarHours: fast polling in EDGAR's filing window, slow outside it, never late for the opening."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from smt.worker.schedules import EdgarHours

ET = ZoneInfo("America/New_York")
POLL = EdgarHours(20, 600)


@pytest.mark.parametrize(("now", "expected"), [
    (datetime(2026, 9, 28, 12, 0, tzinfo=ET), datetime(2026, 9, 28, 12, 0, 20, tzinfo=ET)),  # in the window
    (datetime(2026, 9, 28, 5, 40, tzinfo=ET), datetime(2026, 9, 28, 5, 50, tzinfo=ET)),      # idle step
    # A 10-minute idle step from 05:56 would end at 06:06: the window opens at 06:00.
    (datetime(2026, 9, 28, 5, 56, tzinfo=ET), datetime(2026, 9, 28, 6, 0, tzinfo=ET)),
    (datetime(2026, 9, 26, 12, 0, tzinfo=ET), datetime(2026, 9, 26, 12, 10, tzinfo=ET)),     # Saturday
])
def test_next_poll(now: datetime, expected: datetime) -> None:
    assert POLL.next_after(now) == expected
