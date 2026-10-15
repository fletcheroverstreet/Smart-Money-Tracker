"""A Form 144 seller's profile lists the notices, and search opens the company first.

Round 7 (product critic), two findings on the running site:

* Search leads to the profiles of sellers who file only Form 144s (Metropolitan Life
  Insurance Co, entity 62148: six notices, no Form 3, 4 or 5). The profile said "No
  filings on record here yet." and listed none of them.
* "apple" + Enter opened Sarah E. Apple, with Apple Inc. ninth; "goldman" listed GSCE and
  three preferred classes of Goldman Sachs Group before GS.

The notice and the Form 4 are real EDGAR documents (tests/fixtures/edgar): Benoit
Dageville's notice of a planned sale of 50,000 Snowflake shares, filed 2026-09-09, and
the Form 4 that reported the sale. Loaded without the Form 4, he is a seller with
nothing but a notice on record, which is the case being tested. The 144/A is a
test-only copy of the real notice (its accession starts with TEST). The companies and
people in the search test are typed in here, in the shape the real rows have.
"""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

from smt.api.app import create_app
from smt.db.session import session_scope
from smt.ingest.form144 import load_form144
from smt.ingest.ownership import FilingMeta, load_ownership
from smt.sources.forms.form144 import parse_form144
from smt.sources.forms.ownership import PARSER_VERSION, parse_ownership

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]
FIX = Path(__file__).resolve().parents[1] / "fixtures" / "edgar"
NOTICE = "0001959173-26-006819"
AMENDMENT = "TEST-144A-000001"
FORM4 = "0001821737-26-000013"


def _meta(accession: str, form: str, filed_on: date) -> FilingMeta:
    return FilingMeta(
        accession=accession, form_type=form, filed_on=filed_on,
        accepted_at=datetime(filed_on.year, filed_on.month, filed_on.day, 20, 0, tzinfo=UTC),
        url=f"https://www.sec.gov/Archives/edgar/data/0/{accession.replace('-', '')}/{accession}-index.htm",
        primary_doc_url=None, detected_via="manual", parser_version=PARSER_VERSION, live=False,
    )


async def _load_notice(accession: str = NOTICE, filed_on: date = date(2026, 9, 9)) -> None:
    notice = parse_form144((FIX / "form144" / f"{NOTICE}.xml").read_bytes())
    form = "144"
    if accession != NOTICE:  # a test-only amendment: the real notice restated
        notice, form = dataclasses.replace(notice, submission_type="144/A", previous_accession=NOTICE), "144/A"
    async with session_scope() as session:
        await load_form144(session, _meta(accession, form, filed_on), notice)


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _slug(client: httpx.AsyncClient, query: str) -> str:
    hits = (await client.get("/api/search", params={"q": query})).json()["hits"]
    return next(h for h in hits if h["href"].startswith("/people/"))["href"].removeprefix("/people/")


async def test_a_seller_with_only_a_notice_has_it_on_the_profile(client: httpx.AsyncClient) -> None:
    await _load_notice()
    slug = await _slug(client, "dageville")
    profile = (await client.get(f"/api/people/{slug}")).json()
    stats = profile["stats"]
    assert stats["trades"] == 0
    assert stats["planned_sales"] == 1
    # The notice is a filing on record: the profile's range is its filing date, not empty.
    assert (stats["first_filed"], stats["last_filed"]) == ("2026-09-09", "2026-09-09")
    assert "has filed 1 Form 144 notice of planned sales of Snowflake Inc. shares." in profile["summary"]
    assert "No Form 3, 4 or 5 on record here." in profile["summary"]
    assert "no reported transactions yet" not in profile["summary"]

    notices = (await client.get(f"/api/people/{slug}/planned-sales")).json()
    assert [n["accession"] for n in notices] == [NOTICE]
    notice = notices[0]
    assert notice["issuer"]["name"] == "Snowflake Inc."
    assert notice["seller"]["slug"] == slug
    assert notice["filed_on"] == "2026-09-09"
    assert float(notice["shares"]) == 50_000


async def test_an_amended_notice_is_counted_and_listed_once(client: httpx.AsyncClient) -> None:
    await _load_notice()
    await _load_notice(AMENDMENT, date(2026, 9, 12))
    slug = await _slug(client, "dageville")
    stats = (await client.get(f"/api/people/{slug}")).json()["stats"]
    assert stats["planned_sales"] == 1
    assert (stats["first_filed"], stats["last_filed"]) == ("2026-09-12", "2026-09-12")  # the current version
    notices = (await client.get(f"/api/people/{slug}/planned-sales")).json()
    assert [n["accession"] for n in notices] == [AMENDMENT]


async def test_an_insiders_summary_mentions_the_notices_too(client: httpx.AsyncClient) -> None:
    await _load_notice()
    async with session_scope() as session:
        await load_ownership(session, _meta(FORM4, "4", date(2026, 9, 10)),
                             parse_ownership((FIX / "ownership" / f"{FORM4}.xml").read_bytes()))
    slug = await _slug(client, "dageville")
    profile = (await client.get(f"/api/people/{slug}")).json()
    assert profile["stats"]["open_market_sells"] == 1  # the Form 4's one sale (it has other rows too)
    assert profile["stats"]["planned_sales"] == 1
    assert (profile["stats"]["first_filed"], profile["stats"]["last_filed"]) == ("2026-09-09", "2026-09-10")
    assert profile["summary"].startswith("Benoit Dageville is a Director of Snowflake Inc.")
    assert profile["summary"].endswith("Also on file: 1 Form 144 notice of planned sales of Snowflake Inc. shares.")
    assert "No Form 3, 4 or 5" not in profile["summary"]


async def test_a_profile_with_no_notices_says_nothing_about_them(client: httpx.AsyncClient) -> None:
    async with session_scope() as session:
        await load_ownership(session, _meta(FORM4, "4", date(2026, 9, 10)),
                             parse_ownership((FIX / "ownership" / f"{FORM4}.xml").read_bytes()))
    slug = await _slug(client, "dageville")
    profile = (await client.get(f"/api/people/{slug}")).json()
    assert profile["stats"]["planned_sales"] == 0
    assert "Form 144" not in profile["summary"]
    assert (await client.get(f"/api/people/{slug}/planned-sales")).json() == []


async def _company(name: str, slug: str, *tickers: str) -> None:
    """A company with one security per ticker, as the security master stores share classes."""
    async with session_scope() as session:
        entity_id = (await session.execute(text(
            "INSERT INTO entities (kind, name, sort_name, slug, tags) VALUES ('company', :n, :n, :s, ARRAY['issuer']) "
            "RETURNING id"), {"n": name, "s": slug})).scalar_one()
        for ticker in tickers:
            security_id = (await session.execute(text(
                "INSERT INTO securities (issuer_entity_id, name, primary_ticker, sector_source) "
                "VALUES (:e, :n, :t, 'unmapped') RETURNING id"), {"e": entity_id, "n": name, "t": ticker})).scalar_one()
            await session.execute(text(
                "INSERT INTO security_ids (security_id, scheme, value, source) VALUES (:s, 'ticker', :t, 'test')"),
                {"s": security_id, "t": ticker})


async def test_a_company_named_like_the_query_comes_before_people(client: httpx.AsyncClient) -> None:
    await _company("Apple Inc.", "apple-inc-320193", "AAPL")
    await _company("Apple Hospitality REIT, Inc.", "apple-hospitality-reit-inc-1418121", "APLE")
    await _company("Pineapple Financial Inc.", "pineapple-financial-inc-1938109", "PAPL")
    async with session_scope() as session:
        await session.execute(text(
            "INSERT INTO entities (kind, name, sort_name, slug, tags) VALUES "
            "('person', 'Sarah E. Apple', 'Apple, Sarah E.', 'sarah-e-apple-2132654', ARRAY['insider']), "
            "('person', 'Adam D Appleby', 'Appleby, Adam D', 'adam-d-appleby-1602707', ARRAY['insider'])"))
    hits = (await client.get("/api/search", params={"q": "apple"})).json()["hits"]
    order = [h["href"] for h in hits]
    # Enter opens the first hit: the company, not a person who shares the word.
    assert order[0] == "/tickers/AAPL"
    assert order.index("/tickers/APLE") < order.index("/people/sarah-e-apple-2132654")
    # A company that only contains the word follows the people.
    assert order.index("/people/sarah-e-apple-2132654") < order.index("/tickers/PAPL")
    assert order.index("/people/adam-d-appleby-1602707") < order.index("/tickers/PAPL")
    assert len(order) == len(set(order))


async def test_a_company_is_one_row_pointing_at_its_common_stock(client: httpx.AsyncClient) -> None:
    await _company("GOLDMAN SACHS GROUP INC", "goldman-sachs-group-inc-886982", "GSCE", "GS-PA", "GS-PC", "GS-PD", "GS")
    await _company("Snow Rothschild Acquisition Corp.", "snow-rothschild-acquisition-corp-1", "ISNRU", "ISNRW", "ISNR")
    hits = (await client.get("/api/search", params={"q": "goldman"})).json()["hits"]
    assert [(h["label"], h["sublabel"], h["href"]) for h in hits] == [
        ("Goldman Sachs Group Inc", "GS", "/tickers/GS")]
    hits = (await client.get("/api/search", params={"q": "snow roth"})).json()["hits"]
    assert [h["href"] for h in hits] == ["/tickers/ISNR"]
    # The other classes are found by their ticker.
    hits = (await client.get("/api/search", params={"q": "gs-p"})).json()["hits"]
    assert [h["href"] for h in hits if h["kind"] == "ticker"] == ["/tickers/GS-PA", "/tickers/GS-PC", "/tickers/GS-PD"]
