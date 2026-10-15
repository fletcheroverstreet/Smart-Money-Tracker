"""What the watchdog noticed, and what it told you about it.

The watchdog (smt.worker.watchdog) checks the pipeline every few minutes. Each
problem it finds becomes one row here. The row is the alert log too: it
records whether an alert went out when the problem started and when it ended.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from smt.db.base import Base, BigIntPK

#: Alert states for `open_alert` / `clear_alert`.
#: pending = not sent yet; sent = delivered through Apprise; logged = no Apprise
#: URLs configured, so it went to the log only; failed = gave up after retries;
#: skipped = deliberately not sent (info-level, or never announced); none = not applicable.
ALERT_STATES = ("pending", "sent", "logged", "failed", "skipped", "none")


class WatchdogFinding(Base):
    """One incident: a problem that started (opened) and, later, ended (cleared).

    Two kinds:

    * a **condition** (an EDGAR feed that stopped updating, a stuck queue) stays
      open while it holds and is cleared when it stops holding;
    * an **event** (runs left unfinished by a worker that stopped) is recorded
      already cleared, because there's nothing to wait for.

    At most one row per `key` can be open at a time (a partial unique index).
    That's how the same problem alerts once, instead of every five minutes, and
    alerts again only after it has cleared and come back.
    """

    __tablename__ = "watchdog_findings"
    __table_args__ = (
        Index("ix_watchdog_findings_open_key", "key", unique=True,
              postgresql_where=text("cleared_at IS NULL")),
        Index("ix_watchdog_findings_opened_at", "opened_at"),
    )

    id: Mapped[BigIntPK]
    #: Stable identity of the problem, e.g. "stale_source:edgar.poll_ownership".
    key: Mapped[str] = mapped_column(Text)
    #: Which check found it: stale_source | edgar_queue | quarantine | lease | abandoned_runs | job_failed
    check_name: Mapped[str] = mapped_column(String(40))
    #: condition | event
    kind: Mapped[str] = mapped_column(String(10), server_default="condition")
    #: info | warning | critical
    severity: Mapped[str] = mapped_column(String(10))
    #: The job or source concerned, when there is one.
    subject: Mapped[str | None] = mapped_column(Text)
    #: One plain sentence, e.g. "The EDGAR Forms 3, 4 and 5 feed has not updated in 23 minutes."
    title: Mapped[str] = mapped_column(Text)
    #: Supporting detail in plain words (last success time, counts, examples).
    detail: Mapped[str] = mapped_column(Text, server_default="")
    #: The numbers behind the finding, for the Status page.
    data: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    opened_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    #: The last watchdog run that still saw the problem.
    last_seen_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    cleared_at: Mapped[datetime | None]
    #: Alert about the start of the problem (see ALERT_STATES).
    open_alert: Mapped[str] = mapped_column(String(10), server_default="pending")
    open_alert_at: Mapped[datetime | None]
    #: Alert about the end of the problem (see ALERT_STATES).
    clear_alert: Mapped[str] = mapped_column(String(10), server_default="none")
    clear_alert_at: Mapped[datetime | None]
    #: Failed delivery attempts so far (reset after a success).
    alert_attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    alert_error: Mapped[str | None] = mapped_column(Text)
