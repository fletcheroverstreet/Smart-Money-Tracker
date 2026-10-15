"""Parsing the SEC's reference files: submissions JSON and company_tickers_exchange.json.

Both fixtures are real SEC responses saved on 2026-09-24
(tests/fixtures/edgar/submissions, tests/fixtures/edgar/company_tickers).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from smt.domain.sectors import sector_for
from smt.sources.edgar.reference import TickerFileError, parse_company_tickers
from smt.sources.edgar.submissions import FormerName, SubmissionsError, parse_submissions

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "edgar"
LUNR = FIX / "submissions" / "CIK0001844452.json"
TICKERS = FIX / "company_tickers" / "company_tickers_exchange.sample.json"


# ---------------------------------------------------------------- submissions
def test_submissions_fixture_parses_to_the_fields_we_keep() -> None:
    profile = parse_submissions(LUNR.read_bytes())
    assert profile.cik == "0001844452"
    assert profile.name == "Intuitive Machines, Inc."
    assert profile.entity_type == "operating"
    assert profile.sic == "3812"
    assert profile.sic_description == "Search, Detection, Navigation, Guidance, Aeronautical Sys"
    assert profile.tickers == ("LUNR",)
    assert profile.exchanges == ("Nasdaq",)
    assert profile.state_of_incorporation == "DE"
    assert profile.fiscal_year_end == "1231"
    # The SPAC it used to be. 05:00 UTC is midnight Eastern, so the date is the SEC's date.
    assert profile.former_names == (
        FormerName("Inflection Point Acquisition Corp.", date(2021, 2, 17), date(2023, 2, 10)),
    )
    assert profile.former_names[0].as_json() == {
        "name": "Inflection Point Acquisition Corp.", "from": "2021-02-17", "to": "2023-02-10"}


def _submissions(**overrides: object) -> bytes:
    data: dict[str, object] = {"cik": "1844452", "name": "Example Co", "sic": "3812",
                               "tickers": ["LUNR"], "exchanges": ["Nasdaq"], "formerNames": []}
    data.update(overrides)
    return json.dumps(data).encode()


def test_blank_or_malformed_sic_means_no_sic() -> None:
    assert parse_submissions(_submissions(sic="")).sic is None
    assert parse_submissions(_submissions(sic="N/A")).sic is None
    assert parse_submissions(_submissions(sic=None)).sic is None


def test_tickers_and_exchanges_stay_aligned_when_exchanges_are_short() -> None:
    profile = parse_submissions(_submissions(tickers=["brk-a", "", "BRK-B"], exchanges=["NYSE"]))
    assert profile.tickers == ("BRK-A", "BRK-B")
    # The blank ticker is skipped; BRK-B's exchange position (2) is past the list's end.
    assert profile.exchanges == ("NYSE", None)


def test_former_name_with_unreadable_dates_keeps_the_name() -> None:
    profile = parse_submissions(_submissions(formerNames=[{"name": " Old Name ", "from": "garbage", "to": None},
                                                         {"name": ""}, "not a dict"]))
    assert profile.former_names == (FormerName("Old Name", None, None),)


@pytest.mark.parametrize("payload", [
    b"not json",
    b"[]",
    b'{"name": "no cik"}',
    b'{"cik": "abc"}',
    b'{"cik": "1", "tickers": "LUNR"}',
])
def test_unusable_files_raise_submissions_error(payload: bytes) -> None:
    with pytest.raises(SubmissionsError):
        parse_submissions(payload)


# ---------------------------------------------------------------- company tickers
def test_company_tickers_fixture_parses_every_row() -> None:
    rows = parse_company_tickers(TICKERS.read_bytes())
    assert len(rows) == 10
    assert rows[0] == ("0001652044", "Alphabet Inc.", "GOOGL", "Nasdaq")
    assert ("0001067983", "BERKSHIRE HATHAWAY INC", "BRK-B", "NYSE") in rows
    # The SEC gives no exchange for some tickers: that is None, not "".
    assert ("0002103884", "Compass Sub North, Inc.", "CONE", None) in rows


def test_duplicate_ticker_keeps_the_first_company() -> None:
    payload = json.dumps({"fields": ["cik", "name", "ticker", "exchange"], "data": [
        [1, "First Co", "abc", "NYSE"], [2, "Second Co", "ABC", "Nasdaq"], [3, "No Ticker", "", "NYSE"]]})
    assert parse_company_tickers(payload.encode()) == [("0000000001", "First Co", "ABC", "NYSE")]


@pytest.mark.parametrize("payload", [
    b"<html>maintenance</html>",
    b'{"fields": ["cik", "name"], "data": []}',
    b'{"fields": ["cik", "name", "ticker", "exchange"], "data": {}}',
    b'{"fields": ["cik", "name", "ticker", "exchange"], "data": [["x", "Bad CIK", "BAD", null]]}',
])
def test_unusable_ticker_files_raise(payload: bytes) -> None:
    with pytest.raises(TickerFileError):
        parse_company_tickers(payload)


# ---------------------------------------------------------------- sectors
def test_sector_uses_curated_map_first_then_sic() -> None:
    # DELL is in lodestar's curated map: that wins even when a SIC is known.
    dell = sector_for("DELL", "3571")
    assert (dell.sector, dell.source) == ("Information Technology", "curated")
    # LUNR isn't curated: SIC 3812 falls in the 3800-3899 instruments range.
    lunr = sector_for("LUNR", "3812")
    assert (lunr.sector, lunr.industry, lunr.source) == ("Information Technology", None, "sic")
    # Neither: unmapped, never lumped into "Other".
    assert sector_for("ZZZZ", None).source == "unmapped"
    assert sector_for("ZZZZ", None).sector is None
