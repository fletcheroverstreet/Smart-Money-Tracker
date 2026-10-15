"""Every scheduled job has a limit on the Status page, so a stalled job shows as behind."""

from __future__ import annotations

from smt.api.routers.status import SLA_SECONDS
from smt.worker.jobs import all_jobs


def test_every_scheduled_job_has_a_status_limit() -> None:
    # The catch-up jobs added on 2026-09-29 (edgar.reach_back_*) first shipped without one.
    missing = sorted(job.name for job in all_jobs() if job.name not in SLA_SECONDS)
    assert missing == []
