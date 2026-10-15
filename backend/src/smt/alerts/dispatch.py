"""Send what the watchdog found, once per problem.

The rules, in one place:

* A problem gets **one** message when it starts and **one** when it ends.
  While it stays open, nothing more is sent (the Status page shows the live
  numbers). If it comes back later, that's a new finding and a new message.
* Everything due in one watchdog run goes out as **one combined message**, so
  a restart that trips three checks doesn't send three pings.
* Findings below `MIN_ALERT_SEVERITY` (info) are recorded and shown on the
  Status page, but never pushed.
* If delivery fails (the chat service is down, a URL is wrong), the same
  message is retried on the next run, up to `MAX_DELIVERY_ATTEMPTS` times.
  Nothing is lost silently: the finding keeps `alert_error` and ends as
  `failed`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import or_, select

from smt.alerts.notifier import Alert, Notifier
from smt.alerts.wording import duration_text, when_et
from smt.db.models import WatchdogFinding
from smt.db.session import session_scope
from smt.logs import get_logger

log = get_logger("alerts.dispatch")

#: Order of severities; a finding alerts when its severity is at least MIN_ALERT_SEVERITY.
SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}
MIN_ALERT_SEVERITY = "warning"
#: Give up on a message after this many failed deliveries (one per watchdog run: ~30 min).
MAX_DELIVERY_ATTEMPTS = 6
#: At most this many findings go into one message; the rest wait for the next run.
MAX_PER_MESSAGE = 20


def initial_open_alert(severity: str) -> str:
    """Alert state for a new finding: 'pending' if it should be pushed, else 'skipped'."""
    return "pending" if SEVERITY_RANK.get(severity, 0) >= SEVERITY_RANK[MIN_ALERT_SEVERITY] else "skipped"


def clear_alert_for(open_alert: str) -> str:
    """Alert state when a finding clears: announce the end only if the start was (or will be) announced."""
    return "pending" if open_alert in ("pending", "sent", "logged") else "skipped"


@dataclass(frozen=True)
class DispatchResult:
    """What one dispatch did, for the run record."""

    findings: int = 0
    delivered: bool = False
    channel: str = ""
    error: str | None = None


async def dispatch_alerts(notifier: Notifier, *, status_url: str | None = None,
                          now: datetime | None = None) -> DispatchResult:
    """Send one combined message for every finding whose start or end hasn't been announced."""
    now = now or datetime.now(UTC)
    async with session_scope() as session:
        rows = list((await session.execute(
            select(WatchdogFinding)
            .where(or_(WatchdogFinding.open_alert == "pending", WatchdogFinding.clear_alert == "pending"))
            .order_by(WatchdogFinding.opened_at, WatchdogFinding.id)
            .limit(MAX_PER_MESSAGE)
        )).scalars().all())
        session.expunge_all()
    if not rows:
        return DispatchResult(channel=notifier.delivered_state)

    alert = compose(rows, status_url=status_url)
    error: str | None = None
    try:
        delivered = await notifier.send(alert)
        if not delivered:
            error = "The notification service did not accept the message."
    except Exception as exc:  # a broken chat service must never break the watchdog
        delivered = False
        error = f"{type(exc).__name__}: {exc}"[:500]

    async with session_scope() as session:
        for row in rows:
            finding = await session.get(WatchdogFinding, row.id)
            if finding is None:
                continue
            if delivered:
                if finding.open_alert == "pending":
                    finding.open_alert, finding.open_alert_at = notifier.delivered_state, now
                if finding.clear_alert == "pending":
                    finding.clear_alert, finding.clear_alert_at = notifier.delivered_state, now
                finding.alert_attempts, finding.alert_error = 0, None
                continue
            finding.alert_attempts += 1
            finding.alert_error = error
            if finding.alert_attempts >= MAX_DELIVERY_ATTEMPTS:
                if finding.open_alert == "pending":
                    finding.open_alert = "failed"
                if finding.clear_alert == "pending":
                    finding.clear_alert = "failed"
    if delivered:
        log.info("alerts.delivered", findings=len(rows), channel=notifier.delivered_state)
    else:
        log.error("alerts.delivery_failed", findings=len(rows), error=error)
    return DispatchResult(findings=len(rows), delivered=delivered, channel=notifier.delivered_state,
                          error=error)


def compose(rows: list[WatchdogFinding], *, status_url: str | None = None) -> Alert:
    """One plain-text message covering every finding in `rows`.

    A finding can appear as NEW (started), RESOLVED (ended), or both at once
    when it started and ended between two deliveries.
    """
    new = [r for r in rows if r.open_alert == "pending"]
    resolved = [r for r in rows if r.clear_alert == "pending" and r.cleared_at is not None]
    lines: list[str] = []
    for r in rows:
        started = r.open_alert == "pending"
        ended = r.clear_alert == "pending" and r.cleared_at is not None
        if started and ended and r.kind == "condition":
            assert r.cleared_at is not None
            lines.append(f"STARTED AND RESOLVED ({when_et(r.opened_at)} to {when_et(r.cleared_at)}): {r.title}")
        elif started:
            lines.append(f"NEW ({r.severity}): {r.title}")
            if r.detail:
                lines.append(f"  {r.detail}")
        elif ended:
            assert r.cleared_at is not None
            lines.append(f"RESOLVED after {duration_text(r.cleared_at - r.opened_at)}: {r.title}")
    if status_url:
        lines.append(f"Details: {status_url}")

    parts = []
    if new:
        parts.append(f"{len(new)} new issue" + ("s" if len(new) != 1 else ""))
    if resolved:
        parts.append(f"{len(resolved)} resolved")
    title = "Smart Money Tracker: " + ", ".join(parts or ["update"])

    worst = max((SEVERITY_RANK.get(r.severity, 0) for r in new), default=-1)
    if worst >= SEVERITY_RANK["critical"]:
        level = "critical"
    elif worst >= SEVERITY_RANK["warning"]:
        level = "warning"
    elif resolved and not new:
        level = "success"
    else:
        level = "info"
    return Alert(title=title, body="\n".join(lines), level=level)
