"""The one outbound HTTP client.

Every request to the outside world goes through `HttpClient.get`, which
applies the per-host rate limit, retries with backoff, and understands the
SEC's ways of saying no:

* **403** from sec.gov means "slow down" or "declare yourself", never "not
  found". The page title can even say "Request Rate Threshold Exceeded" at a
  low rate when the User-Agent is missing. All SEC traffic is paused for 11
  minutes (the SEC's penalty is 10) and the request fails loudly.
* **429 / 5xx**, including Akamai's static "maintenance" 503 page: retry with
  exponential backoff and jitter.
* **404**: returned to the caller as a normal response, not retried.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from typing import Final
from urllib.parse import urlparse

import httpx

from smt import __version__
from smt.config import get_settings
from smt.logs import get_logger
from smt.net.ratelimit import Priority, TokenBucket

log = get_logger("http")

SEC_PENALTY_SECONDS: Final = 11 * 60
RETRY_STATUSES: Final = frozenset({429, 500, 502, 503, 504})


#: Most SEC requests allowed back to back before the steady rate applies.
SEC_BURST = 2


class SecThrottled(RuntimeError):
    """The SEC answered 403: we're paused. Never treat this as 'document not found'."""


class FetchError(RuntimeError):
    """A request failed after every retry."""


@dataclass(frozen=True)
class Fetched:
    """A completed response, reduced to what the rest of the system needs."""

    url: str
    status: int
    content: bytes
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def not_modified(self) -> bool:
        return self.status == 304

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())


def bucket_key(url: str) -> str:
    """Which rate-limit bucket a URL belongs to. Every *.sec.gov host shares one."""
    host = urlparse(url).hostname or ""
    return "sec" if host == "sec.gov" or host.endswith(".sec.gov") else host


#: For the live feed: a page that hasn't answered after this many seconds is asked for again
#: alongside the first request, and whichever answers first is used. EDGAR's feed sometimes
#: takes 20+ s to build a page (then serves it from cache at once), and a connection sometimes
#: stalls for a minute; a short timeout would abort the first kind every time, while a second
#: request rescues the second kind without giving up on the first.
FEED_HEDGE_AFTER = 10.0
#: Timeouts for the live feed. At peak EDGAR takes 20-60 s to build a feed page, and with the
#: client's 30 s read timeout both hedged copies of such a page were lost (critic round 6:
#: ReadTimeouts 40 s after the start, and the filings on that page waited for a later poll).
#: So a feed read may wait 60 s for data; a connection that can't even be opened in 5 s is dead.
FEED_TIMEOUT = httpx.Timeout(60.0, connect=5.0)
#: At most this many copies of one feed request per attempt: the first, the hedge, and a
#: replacement started the moment every copy so far has failed (a reset or timed-out
#: connection), instead of after a backoff.
MAX_COPIES = 3


class HttpClient:
    """Async HTTP client with shared rate limits. Create one per process."""

    def __init__(self, *, rates: dict[str, float] | None = None, timeout: float = 30.0,
                 max_attempts: int = 5, transport: httpx.AsyncBaseTransport | None = None) -> None:
        settings = get_settings()
        self._rates = {"sec": settings.sec_rate_limit, **(rates or {})}
        self._buckets: dict[str, TokenBucket] = {}
        self.max_attempts = max_attempts
        self._client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"Accept-Encoding": "gzip, deflate"},
            transport=transport,
            http2=False,
        )

    def _bucket(self, key: str) -> TokenBucket:
        if key not in self._buckets:
            # SEC fair access: an average under 10 requests/s AND no bursts; at most 2 back to back.
            self._buckets[key] = TokenBucket(self._rates.get(key, 2.0), burst=SEC_BURST if key == "sec" else None)
        return self._buckets[key]

    def _headers_for(self, url: str, extra: dict[str, str] | None) -> dict[str, str]:
        headers = dict(extra or {})
        if bucket_key(url) == "sec":
            # SEC fair access: an app name plus a contact email, on every request.
            headers["User-Agent"] = get_settings().require_sec_user_agent()
        else:
            headers.setdefault("User-Agent", f"SmartMoneyTracker/{__version__}")
        return headers

    async def get(self, url: str, *, priority: Priority = Priority.LIVE,
                  headers: dict[str, str] | None = None, hedge_after: float | None = None,
                  timeouts: httpx.Timeout | None = None, attempts: int | None = None) -> Fetched:
        """GET a URL within its host's rate limit, retrying transient failures.

        `hedge_after`: when the request hasn't answered after this many seconds, send the same
        request again (under the same rate limit) and use whichever answers first (the live
        feed, FEED_HEDGE_AFTER). A stalled read once held a Form 4 poll for 84 s, and every
        filing accepted meanwhile waited with it. With hedging on, a copy that fails while no
        other copy is pending is replaced at once, up to MAX_COPIES copies per attempt.

        `timeouts`: this request's timeouts instead of the client's (the feed: FEED_TIMEOUT).
        `attempts`: this request's number of attempts instead of the client's `max_attempts`."""
        key = bucket_key(url)
        bucket = self._bucket(key)
        request_headers = self._headers_for(url, headers)
        last_error: str = ""
        max_attempts = attempts or self.max_attempts
        for attempt in range(1, max_attempts + 1):
            await bucket.acquire(priority)
            try:
                response = await self._send(url, request_headers, bucket, priority, hedge_after, timeouts)
            except httpx.TransportError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                # Logged like a retried status: a timeout used to be retried silently, which hid
                # where a slow poll spent its time.
                log.warning("http.retry", url=url, error=type(exc).__name__, attempt=attempt)
                await self._backoff(attempt)
                continue

            if response.status_code == 403 and key == "sec":
                bucket.pause(SEC_PENALTY_SECONDS)
                log.error("sec.throttled", url=url, body=response.text[:200])
                raise SecThrottled(f"SEC returned 403 for {url}; SEC traffic paused for "
                                   f"{SEC_PENALTY_SECONDS // 60} minutes. Check SEC_USER_AGENT and rates.")
            if response.status_code in RETRY_STATUSES:
                last_error = f"HTTP {response.status_code}"
                retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
                log.warning("http.retry", url=url, status=response.status_code, attempt=attempt)
                await self._backoff(attempt, retry_after)
                continue
            return Fetched(
                url=str(response.url), status=response.status_code, content=response.content,
                headers={k.lower(): v for k, v in response.headers.items()},
            )
        raise FetchError(f"GET {url} failed after {max_attempts} attempts ({last_error})")

    async def _send(self, url: str, headers: dict[str, str], bucket: TokenBucket, priority: Priority,
                    hedge_after: float | None, timeouts: httpx.Timeout | None = None) -> httpx.Response:
        """One attempt. Without `hedge_after`: one request. With it (the live feed):

        * a second copy goes out if no copy has answered `hedge_after` seconds after the newest
          copy was sent (once per attempt);
        * when every copy sent so far has failed (a transport error), a replacement goes out at
          once, so a reset connection doesn't cost a backoff while EDGAR is slow;
        * at most MAX_COPIES copies in all; the first good answer wins and the rest are cancelled.

        Every copy that has finished is read, whenever it finished, including while the hedge
        waited for its rate-limit token. (Pre-review 7: a first copy that answered during that
        wait was never looked at. The read then waited for the hedge, sent a replacement for a
        page it already had, and could raise FetchError with a 200 in hand.) So the hedge's token
        wait races the pending copies, and the hedge goes out only if the token comes first.

        An answer with a retryable status (503, say) wins only when no other copy is still
        pending: a copy still waiting for the page is the better bet. Raises only when every copy
        failed. When more than one copy went out, `http.hedge_result` logs the winner and each
        copy's time (seconds, None for a copy cancelled because another won)."""
        loop = asyncio.get_running_loop()
        copies: list[asyncio.Task[httpx.Response]] = []
        began: list[float] = []
        outcome: list[str | None] = []   # per copy: "ok", a status, an error name, or None (not read)
        took: list[float | None] = []    # per copy: seconds until it finished; None if cancelled
        fallback: httpx.Response | None = None
        error: BaseException | None = None

        def send_copy() -> None:
            index = len(copies)
            began.append(loop.time())
            outcome.append(None)
            took.append(None)
            task = asyncio.create_task(self._request(url, headers, timeouts))

            def finished(done_task: asyncio.Task[httpx.Response]) -> None:
                # Timed when the copy finishes, not when the loop below reads it (which can be
                # later, after a token wait).
                if not done_task.cancelled():
                    took[index] = loop.time() - began[index]

            task.add_done_callback(finished)
            copies.append(task)

        def report(winner: int | None) -> None:
            if len(copies) > 1:
                log.info("http.hedge_result", url=url, winner=winner, copies=len(copies),
                         seconds=[None if t is None else round(t, 2) for t in took], outcomes=outcome)

        def read_finished() -> int | None:
            """Read every finished copy not read yet, oldest first. Returns the index of the first
            good answer; a failure or a retryable answer is kept for later."""
            nonlocal fallback, error
            for i, task in enumerate(copies):
                if outcome[i] is not None or not task.done():
                    continue
                # The done-callback normally times the copy; if this read got in first (the copy
                # finished in the same turn of the loop), time it here so the log never shows a
                # finished copy without its seconds.
                if took[i] is None and not task.cancelled():
                    took[i] = loop.time() - began[i]
                exc: BaseException | None = asyncio.CancelledError() if task.cancelled() else task.exception()
                if exc is not None:
                    error = exc
                    outcome[i] = type(exc).__name__
                    continue
                response = task.result()
                if response.status_code in RETRY_STATUSES:
                    outcome[i] = str(response.status_code)
                    fallback = response
                    continue
                outcome[i] = "ok"
                return i
            return None

        async def token_before_answer(pending: list[asyncio.Task[httpx.Response]]) -> bool:
            """Wait for a rate-limit token while the pending copies keep running. True when the
            token came and no copy finished meanwhile. Otherwise the token request is withdrawn
            (or, when both happened at once, the token goes unused: one request fewer, never one
            more) and the caller reads the finished copy first."""
            token = asyncio.ensure_future(bucket.acquire(priority))
            try:
                await asyncio.wait([token, *pending], return_when=asyncio.FIRST_COMPLETED)
            finally:
                if not token.done():
                    token.cancel()
            return token.done() and not token.cancelled() and not any(task.done() for task in pending)

        send_copy()
        hedged = hedge_after is None       # no timed hedge without hedge_after
        try:
            while True:
                winner = read_finished()
                if winner is not None:
                    report(winner + 1)
                    return copies[winner].result()
                pending = [task for task in copies if not task.done()]
                if not pending:
                    if fallback is not None:
                        report(None)
                        return fallback       # only retryable answers: the caller backs off
                    if hedge_after is None or len(copies) >= MAX_COPIES:
                        break
                    # Every copy has finished and was read just above, so no answer can arrive
                    # (and be missed) while this token is awaited.
                    await bucket.acquire(priority)
                    log.info("http.hedge", url=url, reason="replacement", copy=len(copies) + 1)
                    send_copy()
                    continue
                # The hedge timer runs from the newest copy's start: a replacement just sent
                # deserves the same `hedge_after` as the first copy got.
                wait_for = None if hedged else max(0.0, began[-1] + (hedge_after or 0.0) - loop.time())
                done, _ = await asyncio.wait(pending, timeout=wait_for, return_when=asyncio.FIRST_COMPLETED)
                if done or hedged:
                    continue                  # read at the top of the loop
                hedged = True
                if len(copies) < MAX_COPIES and await token_before_answer(pending):
                    log.info("http.hedge", url=url, after_seconds=hedge_after)
                    send_copy()
            report(None)
            assert error is not None
            raise error
        finally:
            for task in copies:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*copies, return_exceptions=True)

    async def _request(self, url: str, headers: dict[str, str], timeouts: httpx.Timeout | None) -> httpx.Response:
        """One HTTP GET, with this request's timeouts or the client's."""
        if timeouts is None:
            return await self._client.get(url, headers=headers)
        return await self._client.get(url, headers=headers, timeout=timeouts)

    @staticmethod
    async def _backoff(attempt: int, minimum: float | None = None) -> None:
        delay = min(60.0, (2 ** (attempt - 1)) + random.uniform(0, 1))
        await asyncio.sleep(max(delay, minimum or 0.0))

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> HttpClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()


def _retry_after_seconds(value: str | None) -> float | None:
    """Retry-After may be seconds or an HTTP date; only honour the numeric form, capped."""
    if not value:
        return None
    try:
        return min(float(value), 120.0)
    except ValueError:
        return None


_shared: HttpClient | None = None


def shared_client() -> HttpClient:
    """The process-wide client (the worker is the only process that fetches)."""
    global _shared
    if _shared is None:
        _shared = HttpClient()
    return _shared


async def close_shared_client() -> None:
    global _shared
    if _shared is not None:
        await _shared.aclose()
        _shared = None
