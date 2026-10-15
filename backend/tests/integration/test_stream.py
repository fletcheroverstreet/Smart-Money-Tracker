"""The live stream: a committed feed event + NOTIFY reaches subscribers; uncommitted ones don't."""

from __future__ import annotations

import asyncio
from datetime import date

import pytest
from sqlalchemy import text

from smt.api.routers.stream import Broadcaster, _replay
from smt.db.session import session_scope
from smt.ingest.feed import notify_feed, upsert_feed_event

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]


async def _filing_id() -> int:
    async with session_scope() as s:
        return int((await s.execute(text(
            "INSERT INTO filings (source, source_key, form_type, url, detected_via) "
            "VALUES ('edgar', '0000000000-26-000001', '4', 'https://www.sec.gov/x', 'feed') RETURNING id"))).scalar_one())


async def _event(filing_id: int, key: str, *, notify: bool = True, live: bool = True) -> int:
    async with session_scope() as s:
        event_id = await upsert_feed_event(
            s, event_key=key, event_type="insider_trade", source="edgar", form_type="4", section="people",
            filing_id=filing_id, payload={"code": "P"}, occurred_on=date(2026, 9, 24), is_live=live)
        assert event_id is not None
        if notify:
            await notify_feed(s, [event_id])
    return event_id


async def _failing_load(filing_id: int) -> None:
    """A load that queues a NOTIFY and then fails, so its transaction rolls back."""
    async with session_scope() as s:
        event_id = await upsert_feed_event(
            s, event_key="k2", event_type="insider_trade", source="edgar", form_type="4",
            section="people", filing_id=filing_id, payload={})
        assert event_id is not None
        await notify_feed(s, [event_id])
        raise RuntimeError("load failed after the NOTIFY was queued")


async def test_notify_reaches_a_subscriber() -> None:
    filing = await _filing_id()
    hub = Broadcaster()
    queue = hub.subscribe()
    try:
        await asyncio.sleep(0.5)  # let the LISTEN connection come up
        event_id = await _event(filing, "k1")
        items = await asyncio.wait_for(queue.get(), timeout=10)
        assert [i.id for i in items] == [event_id]
        assert items[0].payload == {"code": "P"}
    finally:
        hub.unsubscribe(queue)
        await hub.stop()


async def test_rolled_back_event_is_never_announced() -> None:
    filing = await _filing_id()
    hub = Broadcaster()
    queue = hub.subscribe()
    try:
        await asyncio.sleep(0.5)
        with pytest.raises(RuntimeError):
            await _failing_load(filing)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(queue.get(), timeout=1.5)
    finally:
        hub.unsubscribe(queue)
        await hub.stop()


async def test_replay_covers_a_window_before_the_last_seen_event() -> None:
    filing = await _filing_id()
    first = await _event(filing, "a", notify=False)
    second = await _event(filing, "b", notify=False)
    await _event(filing, "hidden", notify=False, live=False)
    replayed = [i.id for i in await _replay(str(second))]
    assert first in replayed   # the overlap window re-sends recent events; the client dedupes
    assert second in replayed
    assert len(replayed) == 2  # backfill (non-live) events are never replayed


async def test_a_client_that_falls_behind_is_cut_off_so_it_can_replay() -> None:
    from smt.api.routers.stream import QUEUE_SIZE, Broadcaster

    b = Broadcaster()
    slow = b.subscribe()
    for _ in range(QUEUE_SIZE + 1):
        b._publish([])
    assert b.was_dropped(slow)
    b.unsubscribe(slow)
    assert not b.was_dropped(slow)
