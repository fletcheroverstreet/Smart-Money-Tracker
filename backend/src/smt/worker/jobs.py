"""The job registry: every scheduled job the worker runs, in one place.

Each source adds its jobs here. The worker is the only process that fetches
from the outside world (PLAN.md §3, DEC-002).
"""

from __future__ import annotations

import importlib
from datetime import time, timedelta

from smt.worker.scheduler import Job, JobContext
from smt.worker.schedules import DailyAt, EdgarHours, Every


async def refresh_person_stats(ctx: JobContext) -> None:
    """Refresh the People directory's precomputed counts (migration 0008), without blocking reads."""
    from sqlalchemy import text

    from smt.db.session import session_scope

    async with session_scope() as session:
        await session.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY entity_trade_stats"))


async def heartbeat(ctx: JobContext) -> None:
    """Proves the worker is alive. /status reports its last run time."""
    ctx.stats["alive"] = True


def all_jobs() -> list[Job]:
    """Every job, with its schedule. Imports are local so a broken source can't stop the rest."""
    jobs: list[Job] = [
        Job("worker.heartbeat", "worker", heartbeat, Every(60), lease=timedelta(minutes=2), run_at_start=True),
        Job("people.refresh_stats", "people", refresh_person_stats, Every(300), lease=timedelta(minutes=10),
            run_at_start=True),
        # Every 5 minutes: stale feeds, queue backlog, quarantine, stuck leases, failed runs;
        # records findings and alerts through Apprise (smt.worker.watchdog).
        Job("worker.watchdog", "worker", importlib.import_module("smt.worker.watchdog").run_watchdog,
            Every(300), lease=timedelta(minutes=10)),
    ]
    try:
        edgar_jobs = importlib.import_module("smt.sources.edgar.jobs")
    except ModuleNotFoundError:  # pragma: no cover - during early build-out
        return jobs
    jobs += [
        # Form 4 drives the Live page (target: on Live within 2 minutes). Its newest feed page is
        # read every 20 s in filing hours on a fixed cadence: a read starts on time even while up
        # to 2 earlier ones are still waiting for a slow EDGAR page (max_in_flight, see
        # smt.worker.scheduler.Job). The reach-back jobs page further back, and only they move
        # the feed watermarks (smt.sources.edgar.jobs).
        Job("edgar.poll_ownership", "edgar", edgar_jobs.poll_ownership, EdgarHours(20, 600),
            lease=timedelta(minutes=10), run_at_start=True, max_in_flight=3),
        Job("edgar.poll_forms_3_5", "edgar", edgar_jobs.poll_forms_3_5, EdgarHours(60, 600),
            lease=timedelta(minutes=10), run_at_start=True, max_in_flight=3),
        Job("edgar.poll_form144", "edgar", edgar_jobs.poll_form144, EdgarHours(60, 600),
            lease=timedelta(minutes=10), run_at_start=True, max_in_flight=3),
        Job("edgar.reach_back_form4", "edgar", edgar_jobs.reach_back_form4, EdgarHours(60, 600),
            lease=timedelta(minutes=10), run_at_start=True),
        Job("edgar.reach_back_other", "edgar", edgar_jobs.reach_back_other, EdgarHours(120, 600),
            lease=timedelta(minutes=15), run_at_start=True),
        Job("edgar.process_queue", "edgar", edgar_jobs.process_queue, Every(15),
            lease=timedelta(minutes=30), run_at_start=True),
        Job("edgar.reconcile_daily_index", "edgar", edgar_jobs.reconcile_daily_index,
            DailyAt(time(23, 0), days="all"), lease=timedelta(hours=2)),
        Job("edgar.retry_daily_index", "edgar", edgar_jobs.retry_daily_index, Every(3600),
            lease=timedelta(minutes=30)),
        Job("edgar.fill_gaps", "edgar", edgar_jobs.fill_gaps, Every(600), lease=timedelta(minutes=30),
            run_at_start=True),
        Job("edgar.load_new_dataset", "edgar", edgar_jobs.load_new_dataset, DailyAt(time(3, 0), days="all"),
            lease=timedelta(hours=2)),
        Job("edgar.expire_planned_sales", "edgar", edgar_jobs.expire_planned_sales,
            DailyAt(time(4, 0), days="all"), lease=timedelta(minutes=10), run_at_start=True),
        # The security master: tickers and exchanges, then company profiles (SIC, former names) and sectors.
        Job("edgar.refresh_company_tickers", "edgar",
            importlib.import_module("smt.sources.edgar.reference").refresh_company_tickers_job,
            DailyAt(time(5, 30), days="all"), lease=timedelta(minutes=30), run_at_start=True),
        Job("edgar.refresh_submissions", "edgar",
            importlib.import_module("smt.sources.edgar.submissions").refresh_submissions_job,
            DailyAt(time(6, 15), days="all"), lease=timedelta(hours=2)),
        # Public float of companies with large trades: an outside yardstick for trade sizes.
        Job("edgar.refresh_public_float", "edgar",
            importlib.import_module("smt.sources.edgar.public_float").refresh_public_float_job,
            Every(1800), lease=timedelta(minutes=30)),
        # People named from Form 144 free text get their conformed EDGAR name (see edgar/names.py).
        Job("edgar.verify_names", "edgar", importlib.import_module("smt.sources.edgar.names").verify_names,
            DailyAt(time(5, 45), days="all"), lease=timedelta(minutes=30), run_at_start=True),
        # Values that can't be right as filed (a 2029 trade date, a total typed as the price) are
        # flagged so they stay out of rankings and totals (smt.ingest.quality).
        Job("quality.flag_transactions", "quality",
            importlib.import_module("smt.ingest.quality").flag_transactions,
            Every(1800), lease=timedelta(minutes=20), run_at_start=True),
        Job("quality.repair_amendments", "quality",
            importlib.import_module("smt.ingest.quality").repair_amendments_job,
            DailyAt(time(4, 30), days="all"), lease=timedelta(hours=1), run_at_start=True),
    ]
    # Daily prices after the US close: 18:30 ET on NYSE trading days (PLAN.md §7).
    try:
        price_jobs = importlib.import_module("smt.prices.jobs")
    except ModuleNotFoundError:  # pragma: no cover - during early build-out
        return jobs
    jobs.append(Job("prices.refresh_daily", "prices", price_jobs.refresh_daily,
                    DailyAt(time(18, 30), days="trading"), lease=timedelta(hours=3)))
    return jobs
