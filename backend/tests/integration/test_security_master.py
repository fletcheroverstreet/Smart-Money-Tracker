"""The security master: the SEC ticker file, company profiles (submissions JSON) and sectors.

The SEC is mocked with respx and answers with real saved responses
(tests/fixtures/edgar/company_tickers and tests/fixtures/edgar/submissions).
Hand-made rows are used only for the ticker-window rules, where the point is
the shape of the change, not the data.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import func, select, text

from smt.db.models import IssuerProfile, Quarantine, RawDocument, Security, SecurityId
from smt.db.session import session_scope
from smt.net.client import HttpClient
from smt.net.rawstore import RawStore
from smt.sources.edgar import reference, submissions
from smt.sources.edgar.reference import TickerRow, load_company_tickers, refresh_company_tickers
from smt.sources.edgar.submissions import issuers_due, refresh_submissions

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]
FIX = Path(__file__).resolve().parents[1] / "fixtures" / "edgar"
TICKERS_FILE = (FIX / "company_tickers" / "company_tickers_exchange.sample.json").read_bytes()
LUNR_FILE = (FIX / "submissions" / "CIK0001844452.json").read_bytes()
LUNR_URL = submissions.SUBMISSIONS_URL.format(cik="0001844452")
DAY1 = date(2026, 9, 21)
LAST_MODIFIED = "Tue, 22 Sep 2026 10:00:00 GMT"


async def _scalar(sql: str, **params: Any) -> Any:
    async with session_scope() as session:
        return (await session.execute(text(sql), params)).scalar()


async def _windows(ticker: str) -> list[tuple[int, date | None, date | None, str]]:
    """(security_id, valid_from, valid_to, source) for every window of a ticker, oldest first."""
    async with session_scope() as session:
        rows = (await session.execute(
            select(SecurityId.security_id, SecurityId.valid_from, SecurityId.valid_to, SecurityId.source)
            .where(SecurityId.scheme == "ticker", SecurityId.value == ticker)
            .order_by(SecurityId.id))).tuples().all()
    return [tuple(r) for r in rows]  # type: ignore[misc]


async def _security(ticker: str) -> Security:
    async with session_scope() as session:
        return (await session.execute(select(Security).where(Security.primary_ticker == ticker))).scalar_one()


async def _load(rows: list[TickerRow], day: date, **kwargs: Any) -> dict[str, int]:
    async with session_scope() as session:
        return await load_company_tickers(session, rows, today=day, **kwargs)


# ---------------------------------------------------------------- the ticker file
@respx.mock
async def test_ticker_file_is_stored_raw_then_loaded_with_exchanges(raw_dir: Path) -> None:
    route = respx.get(reference.COMPANY_TICKERS_URL).mock(return_value=httpx.Response(
        200, content=TICKERS_FILE, headers={"Last-Modified": LAST_MODIFIED, "Content-Type": "application/json"}))
    async with HttpClient() as client:
        counts = await refresh_company_tickers(client, RawStore(raw_dir), today=DAY1)
    assert counts["outcome"] == "loaded"
    assert (counts["issuers_new"], counts["securities_new"], counts["tickers_opened"]) == (8, 10, 10)
    async with session_scope() as session:
        raw = (await session.execute(select(RawDocument))).scalar_one()
        by_ticker = {s.primary_ticker: s for s in (await session.execute(select(Security))).scalars()}
    assert (raw.doc_role, raw.parse_status, raw.parser_version) == ("company_tickers", "parsed", "2")
    assert raw.last_modified == LAST_MODIFIED
    assert RawStore(raw_dir).get(raw.storage_path) == TICKERS_FILE  # raw first: exact bytes kept
    assert by_ticker["GOOGL"].exchange == "Nasdaq"
    assert by_ticker["BRK-B"].exchange == "NYSE"
    assert by_ticker["CONE"].exchange is None  # the SEC gives none: missing, not guessed
    assert by_ticker["GOOGL"].issuer_entity_id == by_ticker["GOOG"].issuer_entity_id
    assert (by_ticker["DELL"].sector, by_ticker["DELL"].sector_source) == ("Information Technology", "curated")
    assert by_ticker["LUNR"].sector_source == "unmapped"  # until its SEC profile gives a SIC
    assert await _windows("LUNR") == [(by_ticker["LUNR"].id, DAY1, None, "sec_company_tickers")]

    # Next day: the download is conditional, and a 304 changes nothing.
    route.mock(return_value=httpx.Response(304))
    async with HttpClient() as client:
        assert await refresh_company_tickers(client, RawStore(raw_dir)) == {"outcome": "not_modified"}
    assert route.calls.last.request.headers["If-Modified-Since"] == LAST_MODIFIED

    # The same bytes again (a server that ignores If-Modified-Since): not reloaded.
    route.mock(return_value=httpx.Response(200, content=TICKERS_FILE))
    async with HttpClient() as client:
        assert await refresh_company_tickers(client, RawStore(raw_dir)) == {"outcome": "unchanged"}
    assert await _scalar("SELECT count(*) FROM raw_documents") == 1


@respx.mock
async def test_broken_ticker_file_is_quarantined_not_loaded(raw_dir: Path) -> None:
    respx.get(reference.COMPANY_TICKERS_URL).mock(return_value=httpx.Response(
        200, content=b"<html>Down for maintenance</html>"))
    async with HttpClient() as client:
        counts = await refresh_company_tickers(client, RawStore(raw_dir))
    assert counts == {"outcome": "quarantined"}
    async with session_scope() as session:
        raw = (await session.execute(select(RawDocument))).scalar_one()
        q = (await session.execute(select(Quarantine))).scalar_one()
    assert raw.parse_status == "quarantined"
    assert q.raw_document_id == raw.id
    assert q.reason.startswith("company tickers file: not a company tickers file")
    assert await _scalar("SELECT count(*) FROM securities") == 0


# ---------------------------------------------------------------- ticker windows
async def test_loading_the_same_rows_twice_changes_nothing() -> None:
    rows = reference.parse_company_tickers(TICKERS_FILE)
    await _load(rows, DAY1)
    again = await _load(rows, DAY1 + timedelta(days=1))
    assert {k: v for k, v in again.items() if v} == {"tickers_seen": 10}


async def test_renamed_ticker_keeps_the_security() -> None:
    await _load([("0001326801", "Meta Platforms, Inc.", "FB", "Nasdaq")], DAY1)
    fb = await _security("FB")
    day2 = DAY1 + timedelta(days=1)
    counts = await _load([("0001326801", "Meta Platforms, Inc.", "META", "Nasdaq")], day2)
    assert counts["tickers_renamed"] == 1
    assert counts["securities_new"] == 0
    assert await _windows("FB") == [(fb.id, DAY1, DAY1, "sec_company_tickers")]
    assert await _windows("META") == [(fb.id, day2, None, "sec_company_tickers")]
    assert (await _security("META")).id == fb.id


@pytest.mark.parametrize("new_owner_first", [True, False])
async def test_reused_ticker_moves_to_the_new_company(new_owner_first: bool) -> None:
    await _load([("0000000001", "Old Co", "ABC", "NYSE")], DAY1)
    old = await _security("ABC")
    day2 = DAY1 + timedelta(days=1)
    rows: list[TickerRow] = [("0000000002", "New Co", "ABC", "Nasdaq"), ("0000000001", "Old Co", "ABCD", "NYSE")]
    counts = await _load(rows if new_owner_first else rows[::-1], day2)
    assert counts["securities_new"] == 1
    new = await _security("ABC")
    assert new.id != old.id
    # The old company keeps its security under its new symbol; ABC's history is split by date.
    assert (await _security("ABCD")).id == old.id
    assert await _windows("ABC") == [(old.id, DAY1, DAY1, "sec_company_tickers"),
                                     (new.id, day2, None, "sec_company_tickers")]


async def test_vanished_ticker_closes_only_when_the_file_looks_complete() -> None:
    await _load([("0000000001", "Gone Co", "GONE", "NYSE"), ("0000000002", "Stay Co", "STAY", "NYSE")], DAY1)
    day2 = DAY1 + timedelta(days=1)
    # A 1-row file is far below MIN_ROWS_TO_CLOSE: nothing is closed.
    counts = await _load([("0000000002", "Stay Co", "STAY", "NYSE")], day2)
    assert (counts["tickers_closed"], counts["tickers_close_skipped"]) == (0, 1)
    assert (await _windows("GONE"))[0][2] is None
    # Half the tickers vanishing at once is also refused.
    counts = await _load([("0000000002", "Stay Co", "STAY", "NYSE")], day2, min_rows_to_close=1)
    assert counts["tickers_close_skipped"] == 1
    # A believable file: closed, and the security is no longer active.
    counts = await _load([("0000000002", "Stay Co", "STAY", "NYSE")], day2, min_rows_to_close=1,
                         max_vanished_share=0.9)
    assert counts["tickers_closed"] == 1
    gone = await _security("GONE")
    assert await _windows("GONE") == [(gone.id, DAY1, DAY1, "sec_company_tickers")]
    assert gone.is_active is False

    # It comes back for the same company: reopened on the same security.
    day3 = DAY1 + timedelta(days=5)
    counts = await _load([("0000000001", "Gone Co", "GONE", "NYSE"), ("0000000002", "Stay Co", "STAY", "NYSE")],
                         day3)
    assert (counts["tickers_reopened"], counts["securities_new"]) == (1, 0)
    assert await _windows("GONE") == [(gone.id, DAY1, DAY1, "sec_company_tickers"),
                                      (gone.id, day3, None, "sec_company_tickers")]
    assert (await _security("GONE")).is_active is True


async def test_tickers_learned_from_filings_are_matched_and_never_closed_as_vanished() -> None:
    async with session_scope() as session:
        await session.execute(text(
            "INSERT INTO entities (kind, name, sort_name, slug) VALUES ('company', 'Dell', 'Dell', 'dell-1571996')"))
        entity_id = await session.scalar(text("SELECT id FROM entities"))
        await session.execute(text(
            "INSERT INTO entity_ids (entity_id, scheme, value, source) VALUES (:e, 'sec_cik', '0001571996', 'edgar')"),
            {"e": entity_id})
        for ticker in ("DELL", "DELLX"):
            sid = await session.scalar(text(
                "INSERT INTO securities (issuer_entity_id, name, primary_ticker) VALUES (:e, 'Dell', :t) RETURNING id"),
                {"e": entity_id, "t": ticker})
            await session.execute(text(
                "INSERT INTO security_ids (security_id, scheme, value, source) VALUES (:s, 'ticker', :t, 'edgar_filing')"),
                {"s": sid, "t": ticker})
    counts = await _load([("0001571996", "Dell Technologies Inc.", "DELL", "NYSE")], DAY1,
                         min_rows_to_close=1, max_vanished_share=1.0)
    assert (counts["issuers_new"], counts["securities_new"], counts["tickers_closed"]) == (0, 0, 0)
    assert (await _security("DELL")).exchange == "NYSE"
    assert (await _windows("DELLX"))[0][2] is None  # learned from a filing: left alone


# ---------------------------------------------------------------- company profiles and sectors
async def _issuer(cik: str, name: str, *, ticker: str | None, filed_on: date, form: str = "4") -> int:
    """An issuer entity with one insider filing and (optionally) a security."""
    async with session_scope() as session:
        entity_id = int(await session.scalar(text(
            "INSERT INTO entities (kind, name, sort_name, slug) VALUES ('company', :n, :n, :slug) RETURNING id"),
            {"n": name, "slug": f"{name.lower().replace(' ', '-')}-{cik.lstrip('0')}"}))
        await session.execute(text(
            "INSERT INTO entity_ids (entity_id, scheme, value, source) VALUES (:e, 'sec_cik', :c, 'edgar')"),
            {"e": entity_id, "c": cik})
        await session.execute(text(
            "INSERT INTO filings (source, source_key, form_type, filed_on, issuer_entity_id, url, detected_via) "
            "VALUES ('edgar', :k, :f, :d, :e, 'https://www.sec.gov/x', 'manual')"),
            {"k": f"{cik}-26-000001", "f": form, "d": filed_on, "e": entity_id})
        if ticker:
            await session.execute(text(
                "INSERT INTO securities (issuer_entity_id, name, primary_ticker) VALUES (:e, :n, :t)"),
                {"e": entity_id, "n": name, "t": ticker})
    return entity_id


@respx.mock
async def test_profile_is_stored_parsed_and_gives_the_sector(raw_dir: Path) -> None:
    today = datetime.now(UTC).date()
    lunr = await _issuer("0001844452", "Intuitive Machines", ticker="LUNR", filed_on=today - timedelta(days=10))
    route = respx.get(LUNR_URL).mock(return_value=httpx.Response(200, content=LUNR_FILE))
    async with HttpClient() as client:
        counts = await refresh_submissions(client, RawStore(raw_dir), limit=10)
    assert (counts["due"], counts["updated"], counts["securities_relabelled"]) == (1, 1, 1)
    async with session_scope() as session:
        profile = await session.get(IssuerProfile, lunr)
        raw = (await session.execute(select(RawDocument))).scalar_one()
    assert profile is not None
    assert (profile.cik, profile.http_status, profile.sic) == ("0001844452", 200, "3812")
    assert profile.sic_description == "Search, Detection, Navigation, Guidance, Aeronautical Sys"
    assert (profile.tickers, profile.exchanges) == (["LUNR"], ["Nasdaq"])
    assert profile.former_names == [{"name": "Inflection Point Acquisition Corp.",
                                     "from": "2021-02-17", "to": "2023-02-10"}]
    assert (profile.raw_document_id, raw.parse_status, raw.doc_role) == (raw.id, "parsed", "submissions")
    security = await _security("LUNR")
    # Not in the curated map, so the SIC decides; the exchange comes from the profile.
    assert (security.sector, security.sector_source, security.sic) == ("Information Technology", "sic", "3812")
    assert security.exchange == "Nasdaq"

    # Checked recently: not due again.
    async with HttpClient() as client:
        assert (await refresh_submissions(client, RawStore(raw_dir), limit=10))["due"] == 0
    # Due again after REFRESH_DAYS, same bytes: nothing re-applied.
    await _scalar("UPDATE issuer_profiles SET checked_at = now() - interval '31 days' RETURNING 1")
    async with HttpClient() as client:
        again = await refresh_submissions(client, RawStore(raw_dir), limit=10)
    assert (again["unchanged"], again["securities_relabelled"]) == (1, 0)
    assert route.call_count == 2


@respx.mock
async def test_curated_sector_beats_sic_and_missing_or_wrong_files_are_handled(raw_dir: Path) -> None:
    today = datetime.now(UTC).date()
    dell = await _issuer("0001571996", "Dell Technologies", ticker="DELL", filed_on=today - timedelta(days=1))
    gone = await _issuer("0000000404", "No File Co", ticker=None, filed_on=today - timedelta(days=2))
    # The SEC answers with LUNR's file for DELL's CIK: quarantined, never applied to Dell.
    respx.get(submissions.SUBMISSIONS_URL.format(cik="0001571996")).mock(
        return_value=httpx.Response(200, content=LUNR_FILE))
    respx.get(submissions.SUBMISSIONS_URL.format(cik="0000000404")).mock(return_value=httpx.Response(404))
    async with HttpClient() as client:
        counts = await refresh_submissions(client, RawStore(raw_dir), limit=10)
    assert (counts["quarantined"], counts["not_found"]) == (1, 1)
    async with session_scope() as session:
        dell_profile = await session.get(IssuerProfile, dell)
        gone_profile = await session.get(IssuerProfile, gone)
        reason = (await session.execute(select(Quarantine.reason))).scalar_one()
    assert dell_profile is not None
    assert dell_profile.sic is None  # checked, but nothing applied
    assert reason == "submissions file: file is for CIK 0001844452, expected 0001571996"
    assert gone_profile is not None
    assert gone_profile.http_status == 404
    # DELL is curated regardless of SIC.
    await _scalar("UPDATE issuer_profiles SET sic = '3571' WHERE entity_id = :e RETURNING 1", e=dell)
    async with session_scope() as session:
        await submissions.relabel_securities(session)
    security = await _security("DELL")
    assert (security.sector, security.industry, security.sector_source, security.sic) == (
        "Information Technology", "Computer Hardware", "curated", "3571")


async def test_issuers_due_are_recent_insider_issuers_never_profiled_first() -> None:
    today = datetime.now(UTC).date()
    old_profile = await _issuer("0000000001", "Profiled Long Ago", ticker=None, filed_on=today)
    fresh = await _issuer("0000000002", "Never Profiled", ticker=None, filed_on=today - timedelta(days=20))
    await _issuer("0000000003", "Quiet For A Year", ticker=None, filed_on=today - timedelta(days=400))
    await _issuer("0000000004", "Only A 13F", ticker=None, filed_on=today, form="13F-HR")
    recent = await _issuer("0000000005", "Profiled Last Week", ticker=None, filed_on=today)
    await _scalar("INSERT INTO issuer_profiles (entity_id, cik, http_status, checked_at) VALUES "
                  "(:a, '0000000001', 200, now() - interval '40 days'), "
                  "(:b, '0000000005', 200, now() - interval '7 days') RETURNING 1", a=old_profile, b=recent)
    async with session_scope() as session:
        due = await issuers_due(session, limit=10)
        assert due == [(fresh, "0000000002"), (old_profile, "0000000001")]
        assert await issuers_due(session, limit=1) == [(fresh, "0000000002")]
    assert await _scalar("SELECT count(*) FROM issuer_profiles") == 2
    assert await _scalar("SELECT count(*) FROM filings") == 5
    async with session_scope() as session:
        assert (await session.execute(select(func.count()).select_from(Security))).scalar_one() == 0
