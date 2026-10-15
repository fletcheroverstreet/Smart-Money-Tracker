"""Keyset pagination cursors.

A cursor encodes the sort key of the last row returned (for example
detected_at and id), so the next page is "rows after this key". Unlike OFFSET,
the cost stays flat however deep you page, and rows inserted meanwhile don't
shift pages.
"""

from __future__ import annotations

import base64
import json
from datetime import date, datetime
from typing import Any

from fastapi import HTTPException


def encode(*values: Any) -> str:
    raw = json.dumps([v.isoformat() if hasattr(v, "isoformat") else v for v in values], default=str)
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode(cursor: str | None, size: int) -> list[Any] | None:
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        values = json.loads(base64.urlsafe_b64decode(padded.encode()))
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid cursor") from exc
    if not isinstance(values, list) or len(values) != size:
        raise HTTPException(status_code=400, detail="Invalid cursor")
    return values


def _invalid() -> HTTPException:
    return HTTPException(status_code=400, detail="Invalid cursor")


def decode_offset(cursor: str | None) -> int:
    """An offset cursor (paged rankings): a non-negative whole number; 0 when absent."""
    values = decode(cursor, 1)
    if values is None:
        return 0
    if not isinstance(values[0], int) or isinstance(values[0], bool) or values[0] < 0:
        raise _invalid()
    return values[0]


def decode_timestamp_id(cursor: str | None) -> tuple[datetime, int] | None:
    """A (timestamp, id) keyset cursor, as written by encode(detected_at, id)."""
    values = decode(cursor, 2)
    if values is None:
        return None
    try:
        return datetime.fromisoformat(str(values[0])), _positive_id(values[1])
    except (TypeError, ValueError) as exc:
        raise _invalid() from exc


def decode_date_id(cursor: str | None) -> tuple[date, int] | None:
    """A (date, id) keyset cursor, as written by encode(traded_on, id)."""
    values = decode(cursor, 2)
    if values is None:
        return None
    try:
        return date.fromisoformat(str(values[0])), _positive_id(values[1])
    except (TypeError, ValueError) as exc:
        raise _invalid() from exc


def _positive_id(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("not an id")
    return value
