"""Command line: `smt <command>`.

    smt migrate                 apply database migrations
    smt api [--port 8000]       run the read-only API
    smt worker                  run the ingestion worker (the only process that fetches)
    smt run-job NAME            run one scheduled job right now (recorded like any run)
    smt jobs                    list every job and its schedule
    smt backfill ...            queue or run a historical backfill (see --help)
    smt worker-health           exit 1 if the worker is stale (Docker health check)
    smt reprocess [--all]       re-parse stored filings with the current parsers
    smt queue [retry]           show the EDGAR queue; put failed filings back in line
"""

from __future__ import annotations

import argparse
import sys


def _migrate(args: argparse.Namespace) -> None:
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    backend = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "src/smt/db/migrations"))
    command.upgrade(cfg, args.revision)
    print(f"Database migrated to {args.revision}.")


def _api(args: argparse.Namespace) -> None:
    from smt.api.serve import main

    main(host=args.host, port=args.port)


def _worker(_: argparse.Namespace) -> None:
    from smt.worker.main import main

    main()


def _jobs(_: argparse.Namespace) -> None:
    from smt.worker.jobs import all_jobs

    for job in all_jobs():
        print(f"{job.name:36} {job.schedule.describe()}")


def _run_job(args: argparse.Namespace) -> None:
    from smt.logs import configure_logging
    from smt.runtime import run
    from smt.worker.jobs import all_jobs
    from smt.worker.scheduler import run_job_once

    configure_logging()
    jobs = {j.name: j for j in all_jobs()}
    if args.name not in jobs:
        sys.exit(f"Unknown job '{args.name}'. Known: {', '.join(sorted(jobs))}")
    try:
        ctx = run(run_job_once(jobs[args.name]))
    except RuntimeError as exc:
        # The running worker holds this job right now (run_job_once doesn't take it away).
        if "currently leased" not in str(exc):
            raise
        sys.exit(f"{args.name} is running in the worker right now. Try again in a few seconds.")
    print(f"{args.name}: fetched {ctx.docs_fetched}, new rows {ctx.rows_new}, "
          f"parse failures {ctx.parse_failures}, stats {ctx.stats}")


def _backfill(args: argparse.Namespace) -> None:
    import importlib

    from smt.logs import configure_logging
    from smt.runtime import run

    configure_logging()
    backfill = importlib.import_module("smt.sources.edgar.backfill")
    run(backfill.backfill_insider_datasets(quarters=args.quarters, since=args.since, force=args.force))


def _worker_health(_: argparse.Namespace) -> None:
    """Exit 0 if the worker is healthy, 1 (with the reasons) if not. Used by the Docker health check."""
    from smt.api.routers.health import worker_problems
    from smt.db.session import session_scope
    from smt.runtime import run

    async def check() -> list[str]:
        async with session_scope() as session:
            return await worker_problems(session)

    problems = run(check())
    for problem in problems:
        print(f"unhealthy: {problem}")
    sys.exit(1 if problems else 0)


def _reprocess(args: argparse.Namespace) -> None:
    """Re-parse stored filings with the current parsers (no requests to the SEC)."""
    from smt.logs import configure_logging
    from smt.runtime import run
    from smt.sources.edgar.reprocess import reprocess

    configure_logging()
    result = run(reprocess(everything=args.all, form=args.form, accession=args.accession, limit=args.limit))
    print(f"Reprocessed {result.filings:,} filing(s): {result.rows_new:,} new rows, {result.rows_updated:,} updated.")
    for failure in result.failed:
        print(f"  failed: {failure}")
    if result.failed:
        sys.exit(1)


def _backfill_signatures(_: argparse.Namespace) -> None:
    """Store signer names for filings loaded before they were kept, then re-derive owner names."""
    from smt.db.session import session_scope
    from smt.logs import configure_logging
    from smt.runtime import run
    from smt.sources.edgar.names import backfill_signatures, refresh_from_own_filings

    async def go() -> None:
        counts = await backfill_signatures()
        async with session_scope() as session:
            changed = await refresh_from_own_filings(session)
        print(f"Signatures stored: {counts['from_xml']:,} from XML, {counts['from_datasets']:,} from data sets "
              f"({counts['unreadable']:,} unreadable). Names or kinds changed: {changed:,}.")

    configure_logging()
    run(go())


def _queue(args: argparse.Namespace) -> None:
    """Show the EDGAR fetch queue, or put failed filings back in line."""
    from sqlalchemy import text

    from smt.db.session import session_scope
    from smt.runtime import run

    async def go() -> None:
        async with session_scope() as session:
            if args.action == "retry":
                n = (await session.execute(text(
                    "UPDATE edgar_queue SET status = 'pending', attempts = 0, next_attempt_at = now(), "
                    # A fresh start: the 3-day give-up for SEC outages counts from discovered_at.
                    "last_error = NULL, discovered_at = now() WHERE status = 'failed'"))).rowcount  # type: ignore[attr-defined]
                print(f"{n} failed filing(s) put back in the queue.")
            rows = (await session.execute(text(
                "SELECT status, count(*), min(discovered_at) FROM edgar_queue GROUP BY status ORDER BY status"))).all()
            for status, n, oldest in rows:
                print(f"{status:8} {n:>7,}  oldest {oldest:%Y-%m-%d %H:%M}")

    run(go())


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="smt", description="Smart Money Tracker backend")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("migrate", help="apply database migrations")
    p.add_argument("revision", nargs="?", default="head")
    p.set_defaults(func=_migrate)

    p = sub.add_parser("api", help="run the read-only API")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=_api)

    p = sub.add_parser("worker", help="run the ingestion worker")
    p.set_defaults(func=_worker)

    p = sub.add_parser("reprocess", help="re-parse stored filings with the current parsers (no SEC requests)")
    p.add_argument("--all", action="store_true", help="every stored filing, not only those parsed by an older parser")
    p.add_argument("--form", help="only this form type, e.g. 4 or 144")
    p.add_argument("--accession", help="only this filing")
    p.add_argument("--limit", type=int, help="at most this many filings")
    p.set_defaults(func=_reprocess)

    p = sub.add_parser("backfill-signatures",
                       help="store signer names for older filings and re-derive owner names (no SEC requests)")
    p.set_defaults(func=_backfill_signatures)

    p = sub.add_parser("queue", help="show the EDGAR fetch queue; 'retry' puts failed filings back in line")
    p.add_argument("action", nargs="?", choices=["status", "retry"], default="status")
    p.set_defaults(func=_queue)

    p = sub.add_parser("worker-health", help="exit 1 if the worker's heartbeat or EDGAR polling is stale")
    p.set_defaults(func=_worker_health)

    p = sub.add_parser("jobs", help="list scheduled jobs")
    p.set_defaults(func=_jobs)

    p = sub.add_parser("run-job", help="run one job now")
    p.add_argument("name")
    p.set_defaults(func=_run_job)

    p = sub.add_parser("backfill", help="load Forms 3/4/5 history from the SEC's quarterly data sets")
    p.add_argument("--quarters", nargs="*", help="e.g. 2026q2 2026q1 (default: the latest two)")
    p.add_argument("--since", help="load every quarter from this one onward, e.g. 2006q1")
    p.add_argument("--force", action="store_true", help="load quarters again even if already loaded")
    p.set_defaults(func=_backfill)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
