"""Alert wording, message composition and notifier selection. No network: nothing here sends."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from smt.alerts.dispatch import clear_alert_for, compose, initial_open_alert
from smt.alerts.notifier import AppriseNotifier, LogNotifier, notifier_from_settings, split_urls
from smt.alerts.wording import count_text, duration_text, when_et
from smt.config import reset_settings_cache
from smt.db.models import WatchdogFinding

T0 = datetime(2026, 9, 23, 15, 2, tzinfo=UTC)  # 11:02 AM Eastern (EDT)


def test_wording() -> None:
    assert when_et(T0) == "Sep 23, 11:02 AM ET"
    assert when_et(datetime(2026, 12, 1, 5, 0, tzinfo=UTC)) == "Dec 1, 12:00 AM ET"  # EST in winter
    assert duration_text(timedelta(seconds=45)) == "45 seconds"
    assert duration_text(timedelta(minutes=1)) == "1 minute"
    assert duration_text(timedelta(minutes=125)) == "2 hours 5 minutes"
    assert duration_text(timedelta(hours=24)) == "1 day"
    assert duration_text(timedelta(days=3, hours=4, minutes=9)) == "3 days 4 hours"
    assert duration_text(timedelta(seconds=-5)) == "0 seconds"
    assert count_text(1, "filing") == "1 filing"
    assert count_text(1240, "filing") == "1,240 filings"


def test_only_warning_and_above_are_pushed() -> None:
    assert initial_open_alert("info") == "skipped"
    assert initial_open_alert("warning") == "pending"
    assert initial_open_alert("critical") == "pending"
    # The end of a problem is announced only if its start was (or will be).
    assert clear_alert_for("sent") == "pending"
    assert clear_alert_for("logged") == "pending"
    assert clear_alert_for("pending") == "pending"
    assert clear_alert_for("skipped") == "skipped"
    assert clear_alert_for("failed") == "skipped"


def _finding(key: str, severity: str, *, open_alert: str = "pending", clear_alert: str = "none",
             cleared_after: timedelta | None = None, detail: str = "") -> WatchdogFinding:
    return WatchdogFinding(
        key=key, check_name="test", kind="condition", severity=severity, title=f"{key} happened.",
        detail=detail, data={}, opened_at=T0, last_seen_at=T0,
        cleared_at=T0 + cleared_after if cleared_after else None,
        open_alert=open_alert, clear_alert=clear_alert)


def test_compose_one_message_for_new_resolved_and_both() -> None:
    rows = [
        _finding("a", "critical", detail="Last success at 10:40."),
        _finding("b", "warning", open_alert="sent", clear_alert="pending", cleared_after=timedelta(minutes=23)),
        _finding("c", "warning", clear_alert="pending", cleared_after=timedelta(minutes=5)),
    ]
    alert = compose(rows, status_url="http://localhost:3000/status")
    assert alert.title == "Smart Money Tracker: 2 new issues, 2 resolved"
    assert alert.level == "critical"
    assert alert.body.splitlines() == [
        "NEW (critical): a happened.",
        "  Last success at 10:40.",
        "RESOLVED after 23 minutes: b happened.",
        "STARTED AND RESOLVED (Sep 23, 11:02 AM ET to Sep 23, 11:07 AM ET): c happened.",
        "Details: http://localhost:3000/status",
    ]


def test_compose_only_resolved_is_a_success_message() -> None:
    alert = compose([_finding("b", "warning", open_alert="sent", clear_alert="pending",
                              cleared_after=timedelta(hours=2))])
    assert alert.title == "Smart Money Tracker: 1 resolved"
    assert alert.level == "success"


def test_split_urls() -> None:
    assert split_urls("") == []
    assert split_urls(" discord://a/b , ,tgram://c/d ") == ["discord://a/b", "tgram://c/d"]


def test_notifier_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPRISE_URLS", "")
    reset_settings_cache()
    try:
        assert isinstance(notifier_from_settings(), LogNotifier)
        monkeypatch.setenv("APPRISE_URLS", "json://localhost:9/hook")
        reset_settings_cache()
        notifier = notifier_from_settings()
        assert isinstance(notifier, AppriseNotifier)
        assert notifier.accepted == 1
        assert notifier.delivered_state == "sent"
    finally:
        monkeypatch.delenv("APPRISE_URLS")
        reset_settings_cache()


async def test_apprise_with_no_usable_url_reports_not_delivered() -> None:
    notifier = AppriseNotifier(["not-a-url"])
    assert notifier.accepted == 0
    assert await notifier.send(compose([_finding("a", "warning")])) is False


async def test_log_notifier_always_delivers() -> None:
    assert await LogNotifier().send(compose([_finding("a", "warning")])) is True
