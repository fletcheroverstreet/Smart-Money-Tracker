"""Health checks for Docker and an external uptime monitor.

* /healthz: the API is up and can reach the database (503 if not).
* /healthz/worker: the ingestion worker is alive (heartbeat within 5 minutes) and,
  during EDGAR's filing hours, polling for new filings (within 15 minutes). 503 if not,
  so a dead worker can't go unnoticed. `smt worker-health` runs the same check.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from smt import __version__
from smt.api.deps import Session
from smt.domain.business_days import ET, is_sec_business_day

router = APIRouter(tags=["health"])

#: The worker's heartbeat job runs every minute.
HEARTBEAT_MAX_AGE = timedelta(minutes=5)
#: EDGAR polling runs every 60 s in filing hours.
POLL_MAX_AGE = timedelta(minutes=15)


class Health(BaseModel):
    status: str
    version: str
    database: str


class WorkerHealth(BaseModel):
    status: str = "ok"
    problems: list[str]


@router.get("/healthz")
async def healthz(session: Session, response: Response) -> Health:
    """Used by Docker health checks and the external uptime monitor."""
    try:
        await session.execute(text("select 1"))
        database = "ok"
    except Exception:
        database = "unreachable"
    if database != "ok":
        response.status_code = 503
    return Health(status="ok" if database == "ok" else "degraded", version=__version__, database=database)


def in_filing_hours(now: datetime) -> bool:
    """EDGAR accepts filings 06:00-22:00 ET on SEC business days."""
    local = now.astimezone(ET)
    return is_sec_business_day(local.date()) and 6 <= local.hour < 22


async def worker_problems(session: AsyncSession, now: datetime | None = None) -> list[str]:
    """What is wrong with the worker right now, in plain words (empty = healthy)."""
    now = now or datetime.now(UTC)
    rows = dict((await session.execute(text(
        "SELECT name, last_ok_at FROM scheduler_jobs WHERE name IN ('worker.heartbeat', 'edgar.poll_ownership')"
    ))).tuples().all())
    problems = []
    beat = rows.get("worker.heartbeat")
    if beat is None or now - beat > HEARTBEAT_MAX_AGE:
        problems.append("the worker's heartbeat is older than 5 minutes (is the worker running?)")
    poll = rows.get("edgar.poll_ownership")
    if in_filing_hours(now) and (poll is None or now - poll > POLL_MAX_AGE):
        problems.append("no successful EDGAR poll for Forms 3/4/5 in the last 15 minutes during filing hours")
    return problems


@router.get("/healthz/worker")
async def worker_health(session: Session, response: Response) -> WorkerHealth:
    problems = await worker_problems(session)
    if problems:
        response.status_code = 503
    return WorkerHealth(status="ok" if not problems else "unhealthy", problems=problems)
