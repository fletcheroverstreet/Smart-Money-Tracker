"""Loads one-off closure dates from `config/calendars.yaml`.

Federal holidays come from the `holidays` package. One-off closures (national
days of mourning, emergency closures) aren't predictable, so they're listed
by hand in YAML where they're easy to review.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path

import yaml

from smt.config import CONFIG_DIR


@lru_cache(maxsize=1)
def sec_extra_closures(path: Path | None = None) -> frozenset[date]:
    """Dates the SEC/EDGAR was closed that aren't regular federal holidays."""
    file = path or CONFIG_DIR / "calendars.yaml"
    if not file.exists():
        return frozenset()
    data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    entries = data.get("sec_extra_closures", []) or []
    return frozenset(date.fromisoformat(str(e["date"])) for e in entries)
