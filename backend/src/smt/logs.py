"""Structured logging.

JSON lines in production (easy to ship and grep), readable key=value lines in
development. Every log call passes context as keyword arguments rather than
formatting it into the message, so logs can be filtered by field:

    log.info("filing.parsed", accession=acc, form="4", rows=3)
"""

from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(*, json: bool | None = None, level: str = "INFO") -> None:
    """Configure structlog and the stdlib root logger once per process."""
    if json is None:
        json = not sys.stderr.isatty()
    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer(colors=False)
    )
    structlog.configure(
        processors=[*processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(level=level, stream=sys.stderr, format="%(message)s")
    # Third-party libraries are chatty at INFO; keep them at WARNING.
    for noisy in ("httpx", "httpcore", "sqlalchemy.engine", "alembic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a logger bound to a component name."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(component=name)
    return logger
