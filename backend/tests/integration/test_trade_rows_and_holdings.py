"""What trade rows say about totals, and the terms of Form 3 derivative holdings.

Trade rows: every table lists a row even when totals leave it out (a second copy
of a trade another filing reports, an amendment row that couldn't be paired).
Each row says whether it counts (`in_totals`, the same SQL as the totals) and,
for a second copy, which row does count (`duplicate_of`), so a table adds up to
the figures next to it.

Holdings: a Form 3's derivative lines (options, restricted stock units) have no
count of the derivatives themselves; their size is the number of underlying
shares, and they differ by price, dates and footnotes. Those terms are stored
from both sources (the filing's XML and the SEC's quarterly data set) and served
to the quick look.

Real fixtures are loaded through the real loaders. Where a test needs a case the
fixtures don't contain (a general partner's copy, an option with a price and an
expiry), it edits a real document in memory and says so.
"""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from smt.api.app import create_app
from smt.api.routers import tickers as tickers_router
from smt.db.session import session_scope
from smt.ingest.ownership import FilingMeta, load_ownership
from smt.ingest.quality import AMENDMENT_UNPAIRED
from smt.sources.edgar.backfill import load_quarter
from smt.sources.forms.ownership import PARSER_VERSION, parse_ownership

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]
FIX = Path(__file__).resolve().parents[1] / "fixtures" / "edgar"
SNOW = "0001821737-26-000013"   # Form 4: 1 open-market sale (S) of 50,000 sh at $335.72 on 2026-09-09
WSM = "0000719955-26-000140"    # Form 3 (Williams-Sonoma): Table I holdings and 6 restricted stock unit lines
WSM_XML = FIX / "insider_dataset" / f"{WSM}.xml"
DATASET_ZIP = FIX / "insider_dataset" / "2026q2_form345_sample.zip"


def _meta(accession: str, form: str, filed: date) -> FilingMeta:
    return FilingMeta(
        accession=accession, form_type=form, filed_on=filed,
        accepted_at=datetime(filed.year, filed.month, filed.day, 21, 0, tzinfo=UTC),
        url="https://www.sec.gov/", primary_doc_url=None, detected_via="manual",
        parser_version=PARSER_VERSION, live=True,
    )


@pytest.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx.AsyncClient]:
    monkeypatch.setattr(tickers_router, "_today", lambda: date(2026, 9, 24))
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _load_snow(accession: str = SNOW, filed: date = date(2026, 9, 10), *, cik: str | None = None,
                     name: str | None = None, nature: str | None = None) -> None:
    """The SNOW Form 4; optionally re-filed under another accession, owner and direct/indirect nature."""
    doc = parse_ownership((FIX / "ownership" / f"{SNOW}.xml").read_bytes())
    if cik and name:
        doc = dataclasses.replace(doc, owners=(dataclasses.replace(doc.owners[0], cik=cik, name=name),))
    if nature:
        doc = dataclasses.replace(doc, rows=tuple(dataclasses.replace(r, direct_indirect=nature) for r in doc.rows))
    async with session_scope() as session:
        await load_ownership(session, _meta(accession, "4", filed), doc)


async def _sale_ids() -> list[int]:
    """Every SNOW sale row, oldest id first (the earliest copy of a trade is the one that counts)."""
    async with session_scope() as session:
        return [int(i) for i in (await session.execute(text(
            "SELECT id FROM transactions WHERE code = 'S' ORDER BY id"))).scalars()]


async def _get(client: httpx.AsyncClient, path: str, **params: Any) -> Any:
    response = await client.get(path, params=params)
    assert response.status_code == 200, response.text
    return response.json()


async def _snow_sells(client: httpx.AsyncClient) -> int:
    """SNOW's open-market sale count on the ticker index (site-wide totals)."""
    index = await _get(client, "/api/tickers", days=30)
    return int(next(r for r in index["items"] if r["security"]["ticker"] == "SNOW")["sells"])


# ---------------------------------------------------------------- rows left out of totals


async def test_a_general_partners_copy_names_the_counted_copy(client: httpx.AsyncClient) -> None:
    """A fund's sale, reported again a day later by its general partner as indirect ownership."""
    await _load_snow()
    await _load_snow("0009999998-26-000001", date(2026, 9, 11), cik="0009999998", name="Example GP LLC",
                     nature="I")
    counted, copy = await _sale_ids()

    rows = {r["id"]: r for r in (await _get(client, "/api/tickers/SNOW/insiders"))["items"]}
    assert rows[counted]["in_totals"] is True
    assert rows[counted]["duplicate_of"] is None
    assert rows[copy]["in_totals"] is False
    assert rows[copy]["duplicate_of"] == {"trade_id": counted, "owner": rows[counted]["owner"]}
    assert rows[copy]["owner"]["name"] == "Example GP LLC"
    # The ticker table adds up to the ticker's totals: one sale counted, one listed but not counted.
    assert await _snow_sells(client) == sum(1 for r in rows.values() if r["in_totals"]) == 1

    # The quick look for each side.
    copy_detail = await _get(client, f"/api/trades/{copy}")
    assert copy_detail["trade"]["duplicate_of"]["trade_id"] == counted
    assert copy_detail["trade"]["in_totals"] is False
    assert copy_detail["copies"] == []
    counted_detail = await _get(client, f"/api/trades/{counted}")
    assert counted_detail["trade"]["in_totals"] is True
    assert counted_detail["copies"] == [{"trade_id": copy, "owner": rows[copy]["owner"]}]

    # On the general partner's own page the sale counts: it's their trade too, and they aren't
    # listed on the fund's copy (PERSON_COUNTED). The row and the page's stats agree.
    gp = rows[copy]["owner"]["slug"]
    gp_rows = (await _get(client, f"/api/people/{gp}/trades"))["items"]
    assert [(r["id"], r["in_totals"]) for r in gp_rows] == [(copy, True)]
    assert (await _get(client, f"/api/people/{gp}"))["stats"]["open_market_sells"] == 1


async def test_the_same_trade_filed_twice_by_one_owner_counts_once_on_their_page(
        client: httpx.AsyncClient) -> None:
    await _load_snow()
    await _load_snow("0001821737-26-999999", date(2026, 9, 11))  # the same document under another accession
    counted, copy = await _sale_ids()

    owner_rows = (await _get(client, "/api/tickers/SNOW/insiders"))["items"]
    slug = owner_rows[0]["owner"]["slug"]
    person_rows = {r["id"]: r for r in (await _get(client, f"/api/people/{slug}/trades"))["items"]}
    assert person_rows[counted]["in_totals"] is True
    assert person_rows[copy]["in_totals"] is False
    # The counted copy is their own earlier filing: same owner on both.
    assert person_rows[copy]["duplicate_of"] == {"trade_id": counted, "owner": person_rows[counted]["owner"]}
    stats = (await _get(client, f"/api/people/{slug}"))["stats"]
    assert stats["open_market_sells"] == sum(1 for r in person_rows.values() if r["in_totals"]) == 1


async def test_a_copy_of_a_copy_names_the_copy_that_counts(client: httpx.AsyncClient) -> None:
    """Links between copies don't always chain; every copy still names the row that counts.

    As in real data: the general partner reports the fund's sale a day later (indirect, linked
    to the fund's row), then files it again ten days later. The re-filing shares an owner with
    the partner's first report, but not with the fund's row, and it is too late to count as
    the fund's report, so mark_duplicates points it at the partner's row, not the fund's.
    """
    await _load_snow()
    await _load_snow("0009999998-26-000001", date(2026, 9, 11), cik="0009999998", name="Example GP LLC",
                     nature="I")
    await _load_snow("0009999998-26-000002", date(2026, 9, 21), cik="0009999998", name="Example GP LLC",
                     nature="I")
    counted, gp_first, gp_again = await _sale_ids()
    async with session_scope() as session:
        pointers = dict((await session.execute(text(
            "SELECT id, duplicate_of_id FROM transactions WHERE code = 'S'"))).tuples().all())
    assert pointers == {counted: None, gp_first: counted, gp_again: gp_first}  # the chain itself

    # Site-wide (ticker page, quick look): both copies name the fund's row, the one totals count.
    rows = {r["id"]: r for r in (await _get(client, "/api/tickers/SNOW/insiders"))["items"]}
    fund = rows[counted]["owner"]
    assert [rows[i]["in_totals"] for i in (counted, gp_first, gp_again)] == [True, False, False]
    assert rows[gp_first]["duplicate_of"] == rows[gp_again]["duplicate_of"] == {"trade_id": counted, "owner": fund}
    assert await _snow_sells(client) == 1
    assert (await _get(client, f"/api/trades/{gp_again}"))["trade"]["duplicate_of"]["trade_id"] == counted
    # The counted row lists every other copy, including the one that points at it through another.
    gp = rows[gp_first]["owner"]
    assert (await _get(client, f"/api/trades/{counted}"))["copies"] == [
        {"trade_id": gp_first, "owner": gp}, {"trade_id": gp_again, "owner": gp}]
    assert (await _get(client, f"/api/trades/{gp_first}"))["copies"] == [{"trade_id": gp_again, "owner": gp}]

    # The partner's own page counts the trade once, on their first report: the re-filing names that row.
    gp_rows = {r["id"]: r for r in (await _get(client, f"/api/people/{gp['slug']}/trades"))["items"]}
    assert gp_rows[gp_first]["in_totals"] is True
    assert gp_rows[gp_again]["in_totals"] is False
    assert gp_rows[gp_again]["duplicate_of"] == {"trade_id": gp_first, "owner": gp}
    assert (await _get(client, f"/api/people/{gp['slug']}"))["stats"]["open_market_sells"] == 1


async def test_an_unpaired_amendment_row_is_listed_but_not_counted(client: httpx.AsyncClient) -> None:
    await _load_snow()
    (sale,) = await _sale_ids()
    assert await _snow_sells(client) == 1
    async with session_scope() as session:
        # What the loader does to a 4/A row it can't pair with the row it corrects
        # (smt.ingest.ownership._flag_unpaired): keep it, flag it, leave it out of totals.
        await session.execute(text("UPDATE transactions SET data_issues = array_append(data_issues, :flag) "
                                   "WHERE id = :id"), {"flag": AMENDMENT_UNPAIRED, "id": sale})

    (row,) = (await _get(client, "/api/tickers/SNOW/insiders"))["items"]
    assert row["id"] == sale
    assert row["in_totals"] is False
    assert row["duplicate_of"] is None
    assert row["data_issues"] == [AMENDMENT_UNPAIRED]
    assert (await _get(client, f"/api/trades/{sale}"))["trade"]["in_totals"] is False
    person_rows = (await _get(client, f"/api/people/{row['owner']['slug']}/trades"))["items"]
    assert [r["in_totals"] for r in person_rows] == [False]
    # Nothing counts, and the totals say so.
    assert (await _get(client, f"/api/people/{row['owner']['slug']}"))["stats"]["open_market_sells"] == 0
    index = await _get(client, "/api/tickers", days=30)
    assert all(r["security"]["ticker"] != "SNOW" for r in index["items"])


async def test_rows_other_than_open_market_buys_and_sells_never_count(client: httpx.AsyncClient) -> None:
    """Grants, exercises, tax withholding: listed under "All codes", never in buy/sell totals."""
    async with session_scope() as session:
        dell = "0001193125-26-396718"  # Silver Lake entities in Dell: sales plus other codes
        doc = parse_ownership((FIX / "ownership" / f"{dell}.xml").read_bytes())
        await load_ownership(session, _meta(dell, "4", date(2026, 9, 21)), doc)
        symbol = (await session.execute(text(
            "SELECT primary_ticker FROM securities WHERE primary_ticker IS NOT NULL LIMIT 1"))).scalar_one()
    rows = (await _get(client, f"/api/tickers/{symbol}/insiders", codes="all"))["items"]
    other = [r for r in rows if not r["is_open_market"]]
    market = [r for r in rows if r["is_open_market"]]
    assert other  # option exercises (M)
    assert market  # sales (S)
    assert all(r["in_totals"] is False and r["duplicate_of"] is None for r in other)
    assert all(r["in_totals"] is True for r in market)


# ---------------------------------------------------------------- Form 3 derivative holdings


async def _holding_event(client: httpx.AsyncClient) -> Any:
    async with session_scope() as session:
        event_id = (await session.execute(text(
            "SELECT id FROM feed_events WHERE event_type = 'insider_holding'"))).scalar_one()
    return await _get(client, f"/api/feed/events/{event_id}")


async def test_form3_derivative_terms_are_stored_and_served(client: httpx.AsyncClient) -> None:
    doc = parse_ownership(WSM_XML.read_bytes())
    # Real WSM lines are restricted stock units (no price, dates only in footnotes). Make the
    # first one an option, the kind the critic saw as two identical lines, to cover every term.
    first = next(r for r in doc.rows if r.table == "derivative" and r.kind == "holding")
    option = dataclasses.replace(first, security_title="Stock Option (right to buy)",
                                 conversion_or_exercise_price=Decimal("12.5"), exercise_date=date(2027, 1, 25),
                                 expiration_date=date(2031, 3, 1))
    doc = dataclasses.replace(doc, rows=tuple(option if r is first else r for r in doc.rows))
    async with session_scope() as session:
        await load_ownership(session, _meta(WSM, "3", date(2026, 5, 22)), doc)

    detail = await _holding_event(client)
    derivatives = [h for h in detail["holdings"] if h["is_derivative"]]
    assert len(derivatives) == 6
    assert derivatives[0]["security_title"] == "Stock Option (right to buy)"
    assert Decimal(derivatives[0]["exercise_price"]) == Decimal("12.5")
    assert derivatives[0]["exercise_date"] == "2027-01-25"
    assert derivatives[0]["expiration_date"] == "2031-03-01"
    # The restricted stock units as filed: sized by their underlying shares, no count of their own.
    assert [Decimal(h["underlying_shares"]) for h in derivatives] == [
        Decimal(n) for n in (8300, 6298, 4790, 9452, 11044, 14824)]
    assert {h["underlying_title"] for h in derivatives} == {"Common Stock"}
    assert all(h["shares"] is None for h in derivatives)
    rsu = derivatives[1]
    assert (rsu["exercise_price"], rsu["exercise_date"], rsu["expiration_date"]) == (None, None, None)
    assert rsu["footnote_ids"] == ["F4", "F5", "F3"]
    # The footnotes those lines point at are served with them (they tell the grants apart).
    notes = {n["id"]: n["text"] for n in detail["holding_footnotes"]}
    assert notes["F5"] == "These restricted stock units will vest on 3/21/2027."
    # Table I lines have no derivative terms.
    shares_lines = [h for h in detail["holdings"] if not h["is_derivative"]]
    assert shares_lines
    assert all(h["underlying_shares"] is None and h["exercise_price"] is None for h in shares_lines)
    # Loading again changes nothing.
    async with session_scope() as session:
        await load_ownership(session, _meta(WSM, "3", date(2026, 5, 22)), doc)
    assert await _holding_event(client) == detail


async def test_data_set_form3_lines_keep_their_terms(client: httpx.AsyncClient) -> None:
    """History loaded from the SEC's quarterly data set (DERIV_HOLDING) fills the same columns."""
    await load_quarter(DATASET_ZIP)
    async with session_scope() as session:
        rows = (await session.execute(text(
            "SELECT h.underlying_title, h.underlying_shares FROM insider_holdings h "
            "JOIN filings f ON f.id = h.filing_id WHERE f.source_key = :acc AND h.is_derivative "
            "ORDER BY h.line_no"), {"acc": WSM})).all()
    assert [(t, int(n)) for t, n in rows] == [
        ("Common Stock", n) for n in (8300, 6298, 4790, 9452, 11044, 14824)]
