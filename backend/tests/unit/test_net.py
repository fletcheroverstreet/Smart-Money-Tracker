"""The outbound HTTP layer: priorities, SEC throttling, retries, raw storage."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
import respx

from smt.net.client import FetchError, HttpClient, SecThrottled, bucket_key
from smt.net.ratelimit import Priority, TokenBucket
from smt.net.rawstore import RawStore


async def _no_sleep(*_args: object, **_kwargs: object) -> None:
    return None


@pytest.fixture
def fast_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(HttpClient, "_backoff", staticmethod(_no_sleep))


def test_all_sec_hosts_share_one_bucket() -> None:
    assert bucket_key("https://www.sec.gov/cgi-bin/browse-edgar") == "sec"
    assert bucket_key("https://data.sec.gov/submissions/CIK0000320193.json") == "sec"
    assert bucket_key("https://efts.sec.gov/LATEST/search-index") == "sec"
    assert bucket_key("https://disclosures-clerk.house.gov/x.zip") == "disclosures-clerk.house.gov"


async def test_live_requests_jump_ahead_of_backfill() -> None:
    bucket = TokenBucket(rate_per_sec=50, burst=1)
    await bucket.acquire(Priority.BACKFILL)  # drain the single token
    order: list[str] = []

    async def take(name: str, priority: Priority) -> None:
        await bucket.acquire(priority)
        order.append(name)

    backfill = asyncio.create_task(take("backfill", Priority.BACKFILL))
    await asyncio.sleep(0)  # backfill starts waiting first...
    live = asyncio.create_task(take("live", Priority.LIVE))
    await asyncio.gather(backfill, live)
    assert order == ["live", "backfill"]  # ...but live is served first


@respx.mock
async def test_sec_403_pauses_and_raises(fast_backoff: None) -> None:
    respx.get("https://www.sec.gov/x").mock(return_value=httpx.Response(403, text="Undeclared Automated Tool"))
    client = HttpClient()
    with pytest.raises(SecThrottled):
        await client.get("https://www.sec.gov/x")
    assert client._bucket("sec").paused_for > 600
    await client.aclose()


@respx.mock
async def test_sec_requests_carry_the_declared_user_agent() -> None:
    route = respx.get("https://data.sec.gov/y").mock(return_value=httpx.Response(200, content=b"{}"))
    async with HttpClient() as client:
        await client.get("https://data.sec.gov/y")
    agent = route.calls.last.request.headers["User-Agent"]
    assert "@" in agent


@respx.mock
async def test_retries_transient_503_then_succeeds(fast_backoff: None) -> None:
    route = respx.get("https://www.sec.gov/z")
    route.side_effect = [httpx.Response(503, text="maintenance"), httpx.Response(200, content=b"<ok/>")]
    async with HttpClient() as client:
        got = await client.get("https://www.sec.gov/z")
    assert got.ok
    assert got.content == b"<ok/>"
    assert route.call_count == 2


@respx.mock
async def test_gives_up_after_max_attempts(fast_backoff: None) -> None:
    respx.get("https://www.sec.gov/down").mock(return_value=httpx.Response(502))
    async with HttpClient(max_attempts=3) as client:
        with pytest.raises(FetchError, match="3 attempts"):
            await client.get("https://www.sec.gov/down")


@respx.mock
async def test_404_is_returned_not_retried(fast_backoff: None) -> None:
    route = respx.get("https://www.sec.gov/missing").mock(return_value=httpx.Response(404))
    async with HttpClient() as client:
        got = await client.get("https://www.sec.gov/missing")
    assert got.status == 404
    assert route.call_count == 1


def test_raw_store_round_trip_and_dedup(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    content = b"<ownershipDocument>" + b"x" * 5000 + b"</ownershipDocument>"
    sha, rel = store.put(content)
    assert rel == f"{sha[:2]}/{sha[2:4]}/{sha}.zst"
    assert store.get(rel) == content
    assert (tmp_path / rel).stat().st_size < len(content)  # actually compressed
    assert store.put(content) == (sha, rel)                 # idempotent
    assert store.exists(sha)


@respx.mock
async def test_a_transport_retry_is_logged(fast_backoff: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """A timeout used to be retried silently, which hid where a slow poll spent its time."""
    from smt.net import client as client_module

    logged: list[tuple[str, dict[str, object]]] = []

    class Recorder:
        def warning(self, event: str, **fields: object) -> None:
            logged.append((event, fields))

    monkeypatch.setattr(client_module, "log", Recorder())
    route = respx.get("https://www.sec.gov/feed")
    route.side_effect = [httpx.ReadTimeout("no data"), httpx.Response(200, content=b"<feed/>")]
    async with HttpClient() as client:
        got = await client.get("https://www.sec.gov/feed")
    assert got.content == b"<feed/>"
    assert logged == [("http.retry", {"url": "https://www.sec.gov/feed", "error": "ReadTimeout", "attempt": 1})]


@respx.mock
async def test_a_slow_feed_page_is_asked_for_again_and_the_first_answer_wins() -> None:
    """EDGAR's feed can take 20+ s to build a page, and a connection can stall for a minute:
    after `hedge_after` a second request goes out, and whichever answers first is used."""
    calls = 0

    async def answer(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            await asyncio.sleep(5)  # the stalled first request
            return httpx.Response(200, content=b"<slow/>")
        return httpx.Response(200, content=b"<fast/>")

    respx.get("https://www.sec.gov/feed").mock(side_effect=answer)
    async with HttpClient() as client:
        started = asyncio.get_running_loop().time()
        got = await client.get("https://www.sec.gov/feed", hedge_after=0.05)
        took = asyncio.get_running_loop().time() - started
    assert got.content == b"<fast/>"
    assert calls == 2
    assert took < 2  # didn't wait for the stalled request


@respx.mock
async def test_a_quick_answer_sends_only_one_request() -> None:
    route = respx.get("https://www.sec.gov/feed").mock(return_value=httpx.Response(200, content=b"<feed/>"))
    async with HttpClient() as client:
        got = await client.get("https://www.sec.gov/feed", hedge_after=5)
    assert got.content == b"<feed/>"
    assert route.call_count == 1


class _LogRecorder:
    """Stands in for the client's logger and keeps every event."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def info(self, event: str, **fields: object) -> None:
        self.events.append((event, fields))

    def warning(self, event: str, **fields: object) -> None:
        self.events.append((event, fields))

    def named(self, name: str) -> list[dict[str, object]]:
        return [fields for event, fields in self.events if event == name]


@respx.mock
async def test_a_failed_copy_is_replaced_at_once_and_the_outcome_is_logged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Critic round 6: a feed copy that failed (a reset or timed-out connection) used to wait for
    a backoff and a whole new attempt. Now, when no other copy is pending, a replacement goes out
    straight away, and http.hedge_result says which copy won and how long each took."""
    from smt.net import client as client_module

    recorder = _LogRecorder()
    monkeypatch.setattr(client_module, "log", recorder)
    route = respx.get("https://www.sec.gov/feed")
    route.side_effect = [httpx.ReadTimeout("no data"), httpx.Response(200, content=b"<feed/>")]
    async with HttpClient() as client:
        started = asyncio.get_running_loop().time()
        got = await client.get("https://www.sec.gov/feed", hedge_after=5)
        took = asyncio.get_running_loop().time() - started
    assert got.content == b"<feed/>"
    assert route.call_count == 2
    assert took < 2                      # no backoff, and no wait for the 5 s hedge
    assert recorder.named("http.retry") == []
    [result] = recorder.named("http.hedge_result")
    assert result["winner"] == 2
    assert result["copies"] == 2
    assert result["outcomes"] == ["ReadTimeout", "ok"]
    assert all(isinstance(s, float) for s in result["seconds"])  # type: ignore[attr-defined]


@respx.mock
async def test_a_replacement_gets_its_own_hedge_delay() -> None:
    """The timed hedge counts from the newest copy's start. When the first copy fails just
    before the hedge was due, the replacement sent at that moment isn't doubled a split second
    later: it gets the full `hedge_after` to answer before a third copy goes out."""
    loop = asyncio.get_running_loop()
    sent: list[float] = []

    async def answer(request: httpx.Request) -> httpx.Response:
        sent.append(loop.time())
        copy = len(sent)
        if copy == 1:
            await asyncio.sleep(0.3)
            raise httpx.ReadError("connection reset")
        if copy == 2:
            await asyncio.sleep(5)  # the replacement stalls: the hedge must rescue it
        return httpx.Response(200, content=f"<copy{copy}/>".encode())

    respx.get("https://www.sec.gov/feed").mock(side_effect=answer)
    async with HttpClient() as client:
        got = await client.get("https://www.sec.gov/feed", hedge_after=0.4, attempts=1)
    assert got.content == b"<copy3/>"
    assert len(sent) == 3
    assert sent[1] - sent[0] < 0.4           # the replacement went out when copy 1 failed
    assert sent[2] - sent[1] >= 0.35         # and had its own 0.4 s before the hedge


@respx.mock
async def test_a_slow_first_copy_that_wins_is_logged_with_both_times(monkeypatch: pytest.MonkeyPatch) -> None:
    from smt.net import client as client_module

    recorder = _LogRecorder()
    monkeypatch.setattr(client_module, "log", recorder)
    calls = 0

    async def answer(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        copy = calls
        await asyncio.sleep(0.2 if copy == 1 else 5)  # the hedge is slower still
        return httpx.Response(200, content=f"<copy{copy}/>".encode())

    respx.get("https://www.sec.gov/feed").mock(side_effect=answer)
    async with HttpClient() as client:
        got = await client.get("https://www.sec.gov/feed", hedge_after=0.05)
    assert got.content == b"<copy1/>"
    [result] = recorder.named("http.hedge_result")
    assert result["winner"] == 1
    assert result["outcomes"] == ["ok", None]  # the hedge was cancelled
    assert result["seconds"][1] is None  # type: ignore[index]


@respx.mock
async def test_at_most_three_copies_per_attempt(fast_backoff: None) -> None:
    route = respx.get("https://www.sec.gov/feed").mock(side_effect=httpx.ConnectError("refused"))
    async with HttpClient() as client:
        with pytest.raises(FetchError, match="after 1 attempts"):
            await client.get("https://www.sec.gov/feed", hedge_after=5, attempts=1)
    assert route.call_count == 3


@respx.mock
async def test_without_hedging_a_failure_is_not_replaced(fast_backoff: None) -> None:
    """Replacements are for the live feed only; other requests keep one copy per attempt."""
    route = respx.get("https://www.sec.gov/doc").mock(side_effect=httpx.ConnectError("refused"))
    async with HttpClient() as client:
        with pytest.raises(FetchError):
            await client.get("https://www.sec.gov/doc", attempts=2)
    assert route.call_count == 2


@respx.mock
async def test_a_retryable_answer_does_not_beat_a_copy_still_waiting() -> None:
    """A 503 from the hedge must not throw away the first copy, which is still building the page."""
    calls = 0

    async def answer(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            await asyncio.sleep(0.3)
            return httpx.Response(200, content=b"<page/>")
        return httpx.Response(503, text="maintenance")

    respx.get("https://www.sec.gov/feed").mock(side_effect=answer)
    async with HttpClient() as client:
        got = await client.get("https://www.sec.gov/feed", hedge_after=0.05, attempts=1)
    assert got.content == b"<page/>"
    assert calls == 2


@respx.mock
async def test_feed_requests_use_the_long_read_timeout() -> None:
    """Both hedged copies of a page EDGAR took over 30 s to build used to time out (critic round 6):
    a feed read now waits up to 60 s for data, and 5 s for a connection. Other requests keep the
    client's 30 s."""
    from smt.net.client import FEED_TIMEOUT

    feed = respx.get("https://www.sec.gov/feed").mock(return_value=httpx.Response(200, content=b"<feed/>"))
    doc = respx.get("https://www.sec.gov/doc").mock(return_value=httpx.Response(200, content=b"<doc/>"))
    async with HttpClient() as client:
        await client.get("https://www.sec.gov/feed", hedge_after=5, timeouts=FEED_TIMEOUT)
        await client.get("https://www.sec.gov/doc")
    assert feed.calls.last.request.extensions["timeout"]["read"] == 60.0
    assert feed.calls.last.request.extensions["timeout"]["connect"] == 5.0
    assert doc.calls.last.request.extensions["timeout"]["read"] == 30.0


async def test_a_copy_that_answers_while_the_hedge_waits_for_its_token_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pre-review 7 (scratchpad hedge_drop.py): the hedge timer fired, the hedge waited for its
    rate-limit token, and the first copy's 200 arrived during that wait. Nothing ever read it:
    the hedge and then a replacement went out, both failed, and the read raised FetchError
    with the page in hand (3 requests, outcomes [None, 'ConnectError', 'ConnectError']).
    Now the token wait races the copies, and the first copy's answer is returned at once."""
    from smt.net import client as client_module

    recorder = _LogRecorder()
    monkeypatch.setattr(client_module, "log", recorder)
    calls = 0

    async def answer(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            await asyncio.sleep(0.6)   # after the 0.2 s hedge timer, before the next token (2 s)
            return httpx.Response(200, content=b"<copy1/>")
        await asyncio.sleep(0.05)
        raise httpx.ConnectError("reset", request=request)

    # 0.5 requests/s with a bucket of one: the first copy takes the only token, so the hedge
    # waits about 2 s for the next (standing in for a queue of LIVE waiters on the shared bucket).
    async with HttpClient(rates={"example.test": 0.5}, transport=httpx.MockTransport(answer)) as client:
        started = asyncio.get_running_loop().time()
        got = await client.get("https://example.test/feed", hedge_after=0.2, attempts=1)
        took = asyncio.get_running_loop().time() - started
    assert got.content == b"<copy1/>"
    assert 1 <= calls <= 2
    assert took < 1.5                  # returned when copy 1 answered, not after the token
    assert recorder.named("http.hedge") == []          # the token never came first
    assert recorder.named("http.hedge_result") == []   # one copy only, so nothing to compare


async def test_a_copy_that_fails_while_the_hedge_waits_is_replaced_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """The same race with a failure: copy 1's reset arrives while the hedge waits for its token.
    It is read and logged (it used to show as None, 'cancelled because another won'), and only
    one more copy goes out, the replacement, not a hedge and then a replacement."""
    from smt.net import client as client_module

    recorder = _LogRecorder()
    monkeypatch.setattr(client_module, "log", recorder)
    calls = 0

    async def answer(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            await asyncio.sleep(0.6)
            raise httpx.ConnectError("reset", request=request)
        return httpx.Response(200, content=b"<copy2/>")

    async with HttpClient(rates={"example.test": 0.5}, transport=httpx.MockTransport(answer)) as client:
        got = await client.get("https://example.test/feed", hedge_after=0.2, attempts=1)
    assert got.content == b"<copy2/>"
    assert calls == 2
    assert recorder.named("http.hedge") == [{"url": "https://example.test/feed", "reason": "replacement", "copy": 2}]
    [result] = recorder.named("http.hedge_result")
    assert result["winner"] == 2
    assert result["outcomes"] == ["ConnectError", "ok"]
    assert all(isinstance(s, float) for s in result["seconds"])  # type: ignore[attr-defined]
