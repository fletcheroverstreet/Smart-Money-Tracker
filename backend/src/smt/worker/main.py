"""Worker entry point: `smt worker`."""

from __future__ import annotations

import asyncio
import signal
import sys

from smt.db.session import dispose_engines
from smt.logs import configure_logging, get_logger
from smt.runtime import run
from smt.worker.jobs import all_jobs
from smt.worker.scheduler import Scheduler

log = get_logger("worker")


async def _main() -> None:
    scheduler = Scheduler(all_jobs())
    loop = asyncio.get_running_loop()
    if sys.platform != "win32":  # Windows has no loop signal handlers; Ctrl+C raises instead.
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, scheduler.stop)
    try:
        await scheduler.run_forever()
    finally:
        await dispose_engines()


def main() -> None:
    configure_logging()
    log.info("worker.starting")
    try:
        run(_main())
    except KeyboardInterrupt:
        log.info("worker.stopped")
