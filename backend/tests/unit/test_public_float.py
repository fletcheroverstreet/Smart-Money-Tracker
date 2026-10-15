"""Reading the latest public float from the SEC's companyconcept JSON (smt.sources.edgar.public_float)."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from smt.sources.edgar.public_float import latest_public_float


def test_the_latest_reported_float_wins() -> None:
    doc = {"units": {"USD": [
        {"end": "2024-06-30", "val": 1200000, "filed": "2024-09-20", "form": "10-K"},
        {"end": "2025-06-30", "val": 2500000, "filed": "2025-09-18", "form": "10-K"},
        {"end": "2025-06-30", "val": 2600000, "filed": "2025-10-02", "form": "10-K/A"},  # amended later
    ]}}
    assert latest_public_float(json.dumps(doc).encode()) == (Decimal("2600000"), date(2025, 6, 30))


def test_nothing_usable() -> None:
    assert latest_public_float(b"not json") == (None, None)
    assert latest_public_float(json.dumps({"units": {}}).encode()) == (None, None)
