"""Token-bucket rate limiting with priority lanes.

The SEC allows 10 requests per second per IP address, across every machine
and process. A client over that gets 403 responses and a ~10-minute penalty.
All SEC hosts (www, data, efts) share one bucket set below the ceiling
(SEC_RATE_LIMIT, default 8/s).

Priority lanes make sure a long backfill never delays live polling: when a
token frees up, the highest-priority waiter gets it first.
"""

from __future__ import annotations

import asyncio
import heapq
import itertools
import time
from enum import IntEnum


class Priority(IntEnum):
    """Lower value = served first."""

    LIVE = 0            # latest-filings polling and the documents it discovers
    RECONCILE = 1       # nightly daily-index reconciliation
    REFERENCE = 2       # reference data (company tickers, submissions)
    BACKFILL = 3        # historical loads


class TokenBucket:
    """An async token bucket. `acquire()` waits until a request may be sent."""

    def __init__(self, rate_per_sec: float, burst: int | None = None) -> None:
        if rate_per_sec <= 0:
            raise ValueError("rate must be positive")
        self.rate = rate_per_sec
        self.capacity = float(burst if burst is not None else max(1, int(rate_per_sec)))
        self._tokens = self.capacity
        self._updated = time.monotonic()
        self._waiters: list[tuple[int, int, asyncio.Future[None]]] = []
        self._seq = itertools.count()
        self._lock = asyncio.Lock()
        self._paused_until = 0.0
        self._pump: asyncio.Task[None] | None = None

    def pause(self, seconds: float) -> None:
        """Stop issuing tokens for `seconds` (e.g. after the SEC answered 403)."""
        self._paused_until = max(self._paused_until, time.monotonic() + seconds)

    @property
    def paused_for(self) -> float:
        return max(0.0, self._paused_until - time.monotonic())

    async def acquire(self, priority: Priority = Priority.LIVE) -> None:
        """Wait for a token. Higher-priority callers are served first."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()
        async with self._lock:
            heapq.heappush(self._waiters, (int(priority), next(self._seq), future))
            if self._pump is None or self._pump.done():
                self._pump = asyncio.create_task(self._dispatch())
        await future

    def _refill(self) -> None:
        now = time.monotonic()
        self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate)
        self._updated = now

    async def _dispatch(self) -> None:
        """Hand out tokens to waiters in priority order until nobody is waiting."""
        while True:
            async with self._lock:
                # Drop waiters whose callers were cancelled.
                while self._waiters and self._waiters[0][2].done():
                    heapq.heappop(self._waiters)
                if not self._waiters:
                    return
                wait = self.paused_for
                if wait <= 0:
                    self._refill()
                    if self._tokens >= 1:
                        self._tokens -= 1
                        _, _, future = heapq.heappop(self._waiters)
                        future.set_result(None)
                        continue
                    wait = (1 - self._tokens) / self.rate
            await asyncio.sleep(wait)
