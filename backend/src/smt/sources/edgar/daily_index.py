"""Parse EDGAR's daily index (master.YYYYMMDD.idx) for nightly reconciliation.

The master index is pipe-delimited: CIK|Company Name|Form Type|Date Filed|File Name.
Each filing appears once per party (issuer and each owner), so rows are
grouped by accession. It's published after EDGAR closes, at about 22:00-22:30
ET, and days with no filings have no file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from smt.domain.names import pad_cik

_FILE = re.compile(r"edgar/data/(\d+)/(\d{10}-\d{2}-\d{6})\.txt$")


@dataclass(frozen=True)
class IndexedFiling:
    accession: str
    form_type: str
    filed_on: date
    ciks: tuple[str, ...]
    #: (cik, EDGAR conformed name) per party, e.g. ("0001234567", "CUTT TIMOTHY").
    names: tuple[tuple[str, str], ...] = ()


def daily_index_url(day: date) -> str:
    quarter = (day.month - 1) // 3 + 1
    return f"https://www.sec.gov/Archives/edgar/daily-index/{day.year}/QTR{quarter}/master.{day:%Y%m%d}.idx"


def parse_master_index(text: str, forms: frozenset[str]) -> list[IndexedFiling]:
    """Filings of the given exact form types, grouped by accession."""
    by_accession: dict[str, tuple[str, date, list[str]]] = {}
    names: dict[str, dict[str, str]] = {}
    started = False
    for line in text.splitlines():
        if not started:
            started = line.startswith("-----")
            continue
        parts = line.split("|")
        if len(parts) != 5:
            continue
        cik, name, form_type, filed, filename = (p.strip() for p in parts)
        if form_type not in forms:
            continue
        match = _FILE.search(filename)
        if not match:
            continue
        accession = match.group(2)
        filed_on = date(int(filed[:4]), int(filed[4:6]), int(filed[6:8]))
        entry = by_accession.setdefault(accession, (form_type, filed_on, []))
        entry[2].append(pad_cik(cik))
        if name:
            names.setdefault(accession, {})[pad_cik(cik)] = name
    return [IndexedFiling(acc, form, filed, tuple(dict.fromkeys(ciks)), tuple(names.get(acc, {}).items()))
            for acc, (form, filed, ciks) in by_accession.items()]
