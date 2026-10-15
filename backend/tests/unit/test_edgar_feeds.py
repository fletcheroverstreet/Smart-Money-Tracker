"""Atom feed and daily-index parsing against real EDGAR responses saved as fixtures."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from smt.sources.edgar.atom import group_filings, parse_feed
from smt.sources.edgar.daily_index import daily_index_url, parse_master_index
from smt.sources.forms.errors import ParseError

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "edgar"
OWNERSHIP_FORMS = frozenset({"3", "3/A", "4", "4/A", "5", "5/A"})


def test_atom_first_entry_read_by_hand() -> None:
    # First entry of the saved page (fetched 2026-09-24 06:25 EDT):
    # "4 - Chen Yang Luke (0002121461) (Reporting)", AccNo 0001104659-26-110132, updated 06:10:38-04:00.
    entries = parse_feed((FIX / "atom" / "getcurrent_form4_owner_only.xml").read_bytes())
    first = entries[0]
    assert first.accession == "0001104659-26-110132"
    assert first.form_type == "4"
    assert first.cik == "0002121461"
    assert first.name == "Chen Yang Luke"
    assert first.role == "Reporting"
    assert first.accepted_at == datetime(2026, 9, 24, 10, 10, 38, tzinfo=UTC)
    assert first.filed_on == date(2026, 9, 24)
    assert first.index_url.endswith("0001104659-26-110132-index.htm")


def test_atom_groups_parties_by_accession() -> None:
    entries = parse_feed((FIX / "atom" / "getcurrent_form4_owner_only.xml").read_bytes())
    filings = group_filings(entries, OWNERSHIP_FORMS)
    assert len(filings) < len(entries)                       # parties collapse into filings
    assert len({f.accession for f in filings}) == len(filings)
    first = next(f for f in filings if f.accession == "0001104659-26-110132")
    roles = {role for _cik, _name, role in first.parties}
    assert roles == {"Reporting", "Issuer"}


def test_atom_exact_form_filter_drops_prefix_matches() -> None:
    xml = b"""<?xml version="1.0" encoding="ISO-8859-1" ?><feed xmlns="http://www.w3.org/2005/Atom">
    <entry><title>424B2 - BARCLAYS BANK PLC (0000312070) (Filer)</title>
    <link rel="alternate" type="text/html" href="https://www.sec.gov/x-index.htm"/>
    <summary type="html"> &lt;b&gt;Filed:&lt;/b&gt; 2026-09-23 </summary>
    <updated>2026-09-23T17:57:56-04:00</updated>
    <category scheme="https://www.sec.gov/" label="form type" term="424B2"/>
    <id>urn:tag:sec.gov,2008:accession-number=0001918704-26-028586</id></entry></feed>"""
    entries = parse_feed(xml)
    assert len(entries) == 1
    assert group_filings(entries, OWNERSHIP_FORMS) == []     # a prospectus is never an insider trade


def test_atom_rejects_non_feed() -> None:
    with pytest.raises(ParseError):
        parse_feed(b"<html><body>Request Rate Threshold Exceeded</body></html>")


def test_daily_index_groups_parties_and_filters_forms() -> None:
    text = (FIX / "daily_index" / "master.20260922.sample.idx").read_text(encoding="latin-1")
    filings = parse_master_index(text, OWNERSHIP_FORMS | {"144"})
    assert filings, "fixture should contain ownership filings"
    assert all(f.form_type in OWNERSHIP_FORMS | {"144"} for f in filings)
    assert all(f.filed_on == date(2026, 9, 22) for f in filings)
    assert all(len(f.accession) == 20 for f in filings)
    eight_ks = parse_master_index(text, frozenset({"8-K"}))
    assert eight_ks
    assert all(f.form_type == "8-K" for f in eight_ks)
    assert not {f.accession for f in eight_ks} & {f.accession for f in filings}


def test_daily_index_url() -> None:
    assert daily_index_url(date(2026, 9, 22)) == (
        "https://www.sec.gov/Archives/edgar/daily-index/2026/QTR3/master.20260922.idx")
