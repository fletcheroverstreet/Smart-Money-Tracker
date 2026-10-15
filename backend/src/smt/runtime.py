"""Process entry helpers.

psycopg's async driver cannot run on Windows' default ProactorEventLoop, so
every async entry point (worker, API server, CLI commands, tests) starts its
event loop through `run()`, which picks a SelectorEventLoop on Windows. Linux
containers are unaffected.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable, Coroutine
from typing import Any


def loop_factory() -> Callable[[], asyncio.AbstractEventLoop] | None:
    """Return the event-loop factory this platform needs (None = default)."""
    platform = sys.platform  # a plain variable, so type checkers don't prune a branch
    if platform == "win32":
        return asyncio.SelectorEventLoop
    return None


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine to completion on a psycopg-compatible event loop."""
    return asyncio.run(coro, loop_factory=loop_factory())
