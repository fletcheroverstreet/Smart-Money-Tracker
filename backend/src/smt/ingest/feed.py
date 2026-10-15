"""Live feed events and the NOTIFY that pushes them to connected browsers.

A feed event is written in the SAME database transaction as the rows it
describes, so the Live page can never show an item whose data isn't there
yet. NOTIFY is transactional in Postgres: listeners hear about the ids only
after COMMIT. The payload carries ids only, well under the 8,000-byte limit.
The API reads the rows itself.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from smt.db.models import FeedEvent

FEED_CHANNEL = "smt_feed"
_MAX_IDS_PER_NOTIFY = 500


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


async def upsert_feed_event(session: AsyncSession, *, event_key: str, event_type: str, source: str,
                            form_type: str, section: str, filing_id: int, payload: dict[str, Any],
                            transaction_id: int | None = None, entity_id: int | None = None,
                            security_id: int | None = None, size_usd: float | None = None,
                            occurred_on: date | None = None, disclosed_at: datetime | None = None,
                            is_live: bool = True) -> int | None:
    """Insert a feed event once. Returns its id if newly created, None if it already existed.

    Re-ingesting a filing refreshes the payload but never creates a second event
    and never re-announces it.
    """
    stmt = insert(FeedEvent).values(
        event_key=event_key, event_type=event_type, source=source, form_type=form_type,
        section=section, filing_id=filing_id, transaction_id=transaction_id, entity_id=entity_id,
        security_id=security_id, size_usd=size_usd, occurred_on=occurred_on,
        disclosed_at=disclosed_at, is_live=is_live, payload=_jsonable(payload),
    )
    upsert = stmt.on_conflict_do_update(
        index_elements=["event_key"], set_={"payload": stmt.excluded.payload},
    ).returning(FeedEvent.id, text("(xmax = 0) AS inserted"))
    row = (await session.execute(upsert)).one()
    return int(row[0]) if row[1] else None


async def notify_feed(session: AsyncSession, ids: list[int]) -> None:
    """Queue a NOTIFY for new feed event ids (delivered on COMMIT)."""
    for start in range(0, len(ids), _MAX_IDS_PER_NOTIFY):
        chunk = ids[start:start + _MAX_IDS_PER_NOTIFY]
        await session.execute(text("SELECT pg_notify(:channel, :payload)"),
                              {"channel": FEED_CHANNEL, "payload": json.dumps({"ids": chunk})})
