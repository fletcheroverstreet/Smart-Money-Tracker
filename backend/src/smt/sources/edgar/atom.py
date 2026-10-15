"""Parse EDGAR's latest-filings Atom feed (browse-edgar?action=getcurrent).

Verified behaviour (2026-09-23):
* An entry's title is "<form> - <name> (<10-digit CIK>) (<role>)". The role
  can be Reporting, Issuer, Subject, Filed by or Filer, and is sometimes
  missing.
* The same accession appears once per filer role (a Form 4 with four owners
  shows up five times), and the copies can straddle a page boundary. Always
  group by accession.
* `<updated>` is the acceptance time, in ET with an explicit offset.
* `<category term>` is the exact form type. The URL's `type=` filter is a
  PREFIX match (type=4 also returns 424B2 prospectuses), so filter on the
  term, never on the query.
* The filing date appears only inside the HTML summary ("Filed: 2026-09-23").
* The body is ISO-8859-1.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from lxml import etree

from smt.domain.names import pad_cik
from smt.sources.forms.errors import ParseError

ATOM = "{http://www.w3.org/2005/Atom}"
_TITLE = re.compile(r"^(?P<form>.+?) - (?P<name>.*) \((?P<cik>\d{10})\)(?: \((?P<role>[^)]+)\))?\s*$")
_ACCESSION = re.compile(r"accession-number=(\d{10}-\d{2}-\d{6})")
_FILED = re.compile(r"Filed:\s*</b>\s*(\d{4}-\d{2}-\d{2})|Filed:\s*(\d{4}-\d{2}-\d{2})")


@dataclass(frozen=True)
class FeedEntry:
    """One Atom entry: one party's view of one filing."""

    accession: str
    form_type: str
    cik: str
    name: str
    role: str | None
    accepted_at: datetime
    filed_on: date | None
    index_url: str


@dataclass(frozen=True)
class FeedFiling:
    """Entries grouped by accession: one filing with all its parties."""

    accession: str
    form_type: str
    accepted_at: datetime
    filed_on: date | None
    index_url: str
    #: (cik, name, role) for every party that appeared in the feed.
    parties: tuple[tuple[str, str, str | None], ...]

    @property
    def any_cik(self) -> str:
        return self.parties[0][0]


def parse_feed(xml: bytes) -> list[FeedEntry]:
    """Parse one feed page into entries, in feed order (newest first)."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
    try:
        root = etree.fromstring(xml, parser=parser)
    except etree.XMLSyntaxError as exc:
        raise ParseError(f"Atom feed is not valid XML: {exc}") from exc
    if root.tag != f"{ATOM}feed":
        raise ParseError(f"unexpected root element {root.tag!r}")
    entries: list[FeedEntry] = []
    for entry in root.iter(f"{ATOM}entry"):
        title = (entry.findtext(f"{ATOM}title") or "").strip()
        match = _TITLE.match(title)
        entry_id = entry.findtext(f"{ATOM}id") or ""
        acc = _ACCESSION.search(entry_id)
        updated = entry.findtext(f"{ATOM}updated")
        category = entry.find(f"{ATOM}category")
        link = entry.find(f"{ATOM}link")
        if not (match and acc and updated and category is not None):
            continue  # malformed entry: skip it; reconciliation catches anything missed
        summary = entry.findtext(f"{ATOM}summary") or ""
        filed = _FILED.search(summary)
        filed_text = (filed.group(1) or filed.group(2)) if filed else None
        entries.append(FeedEntry(
            accession=acc.group(1),
            form_type=(category.get("term") or match.group("form")).strip(),
            cik=pad_cik(match.group("cik")),
            name=match.group("name").strip(),
            role=match.group("role"),
            accepted_at=datetime.fromisoformat(updated.strip()),
            filed_on=date.fromisoformat(filed_text) if filed_text else None,
            index_url=(link.get("href") if link is not None else "") or "",
        ))
    return entries


def group_filings(entries: list[FeedEntry], forms: frozenset[str]) -> list[FeedFiling]:
    """Group entries by accession, keeping only the exact form types in `forms`."""
    grouped: dict[str, list[FeedEntry]] = {}
    for e in entries:
        if e.form_type in forms:
            grouped.setdefault(e.accession, []).append(e)
    out: list[FeedFiling] = []
    for accession, group in grouped.items():
        first = group[0]
        out.append(FeedFiling(
            accession=accession, form_type=first.form_type, accepted_at=first.accepted_at,
            filed_on=first.filed_on, index_url=first.index_url,
            parties=tuple((e.cik, e.name, e.role) for e in group),
        ))
    return out
