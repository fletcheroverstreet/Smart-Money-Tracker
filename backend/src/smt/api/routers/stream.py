"""The live stream: Server-Sent Events at /api/stream.

The worker writes a feed event and NOTIFYs its id in the same transaction.
This API process holds ONE dedicated LISTEN connection (autocommit, outside
the pool) and fans each notification out to every connected browser through
in-memory queues.

Reconnects: a browser that reconnects sends the Last-Event-ID header, and we
replay live events detected within 2 minutes before that event. The client
drops ids it has already seen. The overlap is deliberate: database ids are
assigned at INSERT, not at COMMIT, so with concurrent writers id 101 can
become visible after id 102 was sent. "ids greater than the last one" would
skip it forever (PLAN.md §7).

Heartbeats every 15 s keep proxies from closing the connection and let the
UI show "updated N s ago".
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
from fastapi import APIRouter, Header, Request
from sqlalchemy import text
from sse_starlette.sse import EventSourceResponse

from smt.api.queries import FEED_SELECT, feed_item
from smt.api.schemas import FeedItem
from smt.config import get_settings
from smt.db.session import session_factory
from smt.ingest.feed import FEED_CHANNEL
from smt.logs import get_logger

router = APIRouter(tags=["stream"])
log = get_logger("api.stream")

HEARTBEAT_SECONDS = 15
REPLAY_WINDOW = timedelta(minutes=2)
QUEUE_SIZE = 200


async def fetch_items(ids: list[int]) -> list[FeedItem]:
    """Display-ready feed rows for these ids (live events only), oldest first."""
    if not ids:
        return []
    async with session_factory()() as session:
        rows = (await session.execute(text(
            FEED_SELECT + " WHERE e.id = ANY(:ids) AND e.is_live ORDER BY e.id"), {"ids": ids})).all()
    return [feed_item(r) for r in rows]


class Broadcaster:
    """One LISTEN connection per process, many subscriber queues."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[list[FeedItem]]] = set()
        self._dropped: set[asyncio.Queue[list[FeedItem]]] = set()
        self._task: asyncio.Task[None] | None = None

    def subscribe(self) -> asyncio.Queue[list[FeedItem]]:
        queue: asyncio.Queue[list[FeedItem]] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._subscribers.add(queue)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._listen())
        return queue

    def unsubscribe(self, queue: asyncio.Queue[list[FeedItem]]) -> None:
        self._subscribers.discard(queue)
        self._dropped.discard(queue)

    def was_dropped(self, queue: asyncio.Queue[list[FeedItem]]) -> bool:
        """True once this client fell too far behind and was cut off."""
        return queue in self._dropped

    @property
    def listeners(self) -> int:
        return len(self._subscribers)

    async def _listen(self) -> None:
        dsn = get_settings().database_url.replace("postgresql+psycopg://", "postgresql://")
        backoff = 1.0
        while self._subscribers:
            try:
                async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
                    await conn.execute(f"LISTEN {FEED_CHANNEL}")
                    backoff = 1.0
                    async for note in conn.notifies(timeout=30):
                        items = await fetch_items(list(json.loads(note.payload).get("ids", [])))
                        if items:
                            self._publish(items)
                    # notifies(timeout=...) ends quietly; loop to re-check subscribers and reconnect.
            except (psycopg.Error, OSError) as exc:
                log.warning("stream.listen_failed", error=str(exc), retry_in=backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    def _publish(self, items: list[FeedItem]) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(items)
            except asyncio.QueueFull:
                # A client that can't keep up is dropped: its stream is ended (see events()), so the
                # browser reconnects and replays what it missed from Last-Event-ID.
                self._subscribers.discard(queue)
                self._dropped.add(queue)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task


broadcaster = Broadcaster()


async def _replay(last_event_id: str | None) -> list[FeedItem]:
    if not last_event_id or not last_event_id.isdigit():
        return []
    async with session_factory()() as session:
        anchor = (await session.execute(text("SELECT detected_at FROM feed_events WHERE id = :id"),
                                        {"id": int(last_event_id)})).scalar_one_or_none()
        if anchor is None:
            return []
        ids = (await session.execute(text(
            "SELECT id FROM feed_events WHERE is_live AND detected_at >= :since ORDER BY detected_at, id "
            "LIMIT 500"), {"since": anchor - REPLAY_WINDOW})).scalars().all()
    return await fetch_items([int(i) for i in ids])


@router.get("/api/stream")
async def stream(request: Request,
                 last_event_id: str | None = Header(default=None, alias="Last-Event-ID")) -> EventSourceResponse:
    """Live feed events. Event types: `feed` (a new disclosure), `heartbeat`, `hello`."""
    queue = broadcaster.subscribe()

    async def events() -> AsyncIterator[dict[str, Any]]:
        try:
            yield {"event": "hello", "data": json.dumps({"server_time": datetime.now(UTC).isoformat()})}
            for item in await _replay(last_event_id):
                yield {"event": "feed", "id": str(item.id), "data": item.model_dump_json()}
            while not await request.is_disconnected():
                if broadcaster.was_dropped(queue):
                    return  # end the stream; the browser reconnects and replays
                try:
                    items = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except TimeoutError:
                    yield {"event": "heartbeat", "data": json.dumps({"server_time": datetime.now(UTC).isoformat()})}
                    continue
                for item in items:
                    yield {"event": "feed", "id": str(item.id), "data": item.model_dump_json()}
        finally:
            broadcaster.unsubscribe(queue)

    return EventSourceResponse(
        events(), ping=None,
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )
