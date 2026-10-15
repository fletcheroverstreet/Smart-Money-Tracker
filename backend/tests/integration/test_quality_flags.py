"""Flagged values stay visible but out of rankings and totals.

Real fixtures are loaded normally, then one SNOW sale is edited to carry the
kind of typing error seen in real filings (the total typed as the price), as
if it had been loaded before the rules existed. The quality job must flag it,
the feed must stop treating it as a size, and totals must ignore it, while the
row itself still shows the numbers as filed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

from smt.api.app import create_app
from smt.api.routers import tickers as tickers_router
from smt.db.session import session_scope
from smt.ingest.ownership import FilingMeta, load_ownership
from smt.ingest.quality import DATE_AFTER_FILING, VALUE_IMPLAUSIBLE, flag_transactions
from smt.sources.forms.ownership import PARSER_VERSION, parse_ownership
from smt.worker.scheduler import JobContext

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]
FIX = Path(__file__).resolve().parents[1] / "fixtures" / "edgar" / "ownership"
SNOW = "0001821737-26-000013"   # 1 sale (S) of 50,000 sh at $335.72 on 2026-09-09
LUNR = "0001213900-26-098884"   # 3 sales (S) on 2026-09-08/09


def _meta(accession: str, filed: date) -> FilingMeta:
    return FilingMeta(
        accession=accession, form_type="4", filed_on=filed,
        accepted_at=datetime(filed.year, filed.month, filed.day, 21, 0, tzinfo=UTC),
        url="https://www.sec.gov/", primary_doc_url=None, detected_via="manual",
        parser_version=PARSER_VERSION, live=True,
    )


@pytest.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx.AsyncClient]:
    monkeypatch.setattr(tickers_router, "_today", lambda: date(2026, 9, 24))
    async with session_scope() as session:
        for accession, filed in ((SNOW, date(2026, 9, 10)), (LUNR, date(2026, 9, 10))):
            doc = parse_ownership((FIX / f"{accession}.xml").read_bytes())
            await load_ownership(session, _meta(accession, filed), doc)
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _snow_sale_id() -> int:
    async with session_scope() as session:
        return int((await session.execute(text(
            "SELECT t.id FROM transactions t JOIN filings f ON f.id = t.filing_id "
            "WHERE f.source_key = :acc AND t.code = 'S'"), {"acc": SNOW})).scalar_one())


async def _run_job() -> JobContext:
    ctx = JobContext(job_name="quality.flag_transactions", run_id=0, cursor={})
    await flag_transactions(ctx)
    return ctx


async def test_clean_fixtures_get_no_flags(client: httpx.AsyncClient) -> None:
    ctx = await _run_job()
    assert ctx.stats["flagged_values"] == 0
    assert ctx.stats["flagged_dates"] == 0
    assert (ctx.stats["closes_stored"], ctx.stats["closes_usable"]) == (0, 0)


async def test_a_total_typed_as_the_price_is_flagged_and_left_out_of_totals(client: httpx.AsyncClient) -> None:
    before = (await client.get("/api/tickers", params={"days": 30})).json()
    snow_before = next(r for r in before["items"] if r["security"]["ticker"] == "SNOW")
    assert Decimal(snow_before["sell_value"]) == Decimal("16786000.00")

    tx_id = await _snow_sale_id()
    async with session_scope() as session:  # the filer typed the total into the price field
        await session.execute(text(
            "UPDATE transactions SET price = 16786000, value = 50000 * 16786000::numeric WHERE id = :id"),
            {"id": tx_id})
        await session.execute(text("UPDATE feed_events SET size_usd = 839300000000 WHERE transaction_id = :id"),
                              {"id": tx_id})
    ctx = await _run_job()
    assert ctx.stats["flagged_values"] == 1

    # The row still shows exactly what was filed, with the flag.
    trade = (await client.get(f"/api/trades/{tx_id}")).json()["trade"]
    assert Decimal(trade["price"]) == Decimal("16786000")
    assert trade["data_issues"] == [VALUE_IMPLAUSIBLE]

    # Totals and rankings ignore it; the ticker still counts the sale.
    after = (await client.get("/api/tickers", params={"days": 30})).json()
    snow = next(r for r in after["items"] if r["security"]["ticker"] == "SNOW")
    assert snow["sells"] == 1
    assert snow["sell_value"] is None
    # It states a price, so it is "left out", not "without a price" (round 6).
    assert (snow["trades_left_out"], snow["sells_left_out"], snow["trades_without_price"]) == (1, 1, 0)

    # The feed no longer treats it as a size, so it can't pass a "$1M+" filter.
    async with session_scope() as session:
        size, payload_issues = (await session.execute(text(
            "SELECT size_usd, payload->'data_issues' FROM feed_events WHERE transaction_id = :id"),
            {"id": tx_id})).one()
    assert size is None
    assert payload_issues == [VALUE_IMPLAUSIBLE]
    big = (await client.get("/api/feed", params={"min_size": 1_000_000})).json()["items"]
    assert all(i["transaction_id"] != tx_id for i in big)

    # Running again changes nothing (idempotent).
    again = await _run_job()
    assert (again.stats["newly_flagged"], again.stats["flags_cleared"]) == (0, 0)
    assert again.stats["flagged_values"] == 1


async def test_a_trade_dated_after_its_filing_loses_its_delay(client: httpx.AsyncClient) -> None:
    tx_id = await _snow_sale_id()
    async with session_scope() as session:
        await session.execute(text("UPDATE transactions SET traded_on = '2029-09-09' WHERE id = :id"), {"id": tx_id})
    await _run_job()
    trade = (await client.get(f"/api/trades/{tx_id}")).json()["trade"]
    assert trade["data_issues"] == [DATE_AFTER_FILING]
    assert trade["delay_days"] is None
    assert trade["is_late"] is None
    # The impossible date can't become anyone's "last trade".
    async with session_scope() as session:
        await session.execute(text("REFRESH MATERIALIZED VIEW entity_trade_stats"))
    directory = (await client.get("/api/people/insiders", params={"sort": "recent"})).json()
    assert directory["items"], "the directory must list the loaded insiders"
    assert any(r.get("last_traded_on") for r in directory["items"])
    last_dates = [r.get("last_traded_on") for r in directory["items"]]
    assert "2029-09-09" not in last_dates


DELL = "0001193125-26-396718"   # 16 open-market sales (S) of Class C common stock on 2026-09-17


async def test_misplaced_decimal_is_flagged_but_another_share_class_is_not() -> None:
    doc = parse_ownership((FIX / f"{DELL}.xml").read_bytes())
    async with session_scope() as session:
        await load_ownership(session, _meta(DELL, date(2026, 9, 21)), doc)
        ids = list((await session.execute(text(
            "SELECT t.id FROM transactions t JOIN filings f ON f.id = t.filing_id "
            "WHERE f.source_key = :acc AND t.code = 'S' ORDER BY t.id"), {"acc": DELL})).scalars())
        typo, other_class = ids[0], ids[1]
        # A decimal point dropped: $15,159 for a ~$150 share (as in a real Ducommun filing).
        await session.execute(text("UPDATE transactions SET price = price * 100 WHERE id = :id"), {"id": typo})
        # A different class at a high price is legitimate (e.g. super-voting shares), with no peers of its own.
        await session.execute(text(
            "UPDATE transactions SET price = price * 100, security_title = 'Class A Super Voting Shares' "
            "WHERE id = :id"), {"id": other_class})
    await _run_job()
    async with session_scope() as session:
        rows = (await session.execute(text(
            "SELECT id, data_issues FROM transactions WHERE id = ANY(:ids)"), {"ids": [typo, other_class]})).all()
        flags = {r.id: list(r.data_issues) for r in rows}
    assert flags[typo] == [VALUE_IMPLAUSIBLE]
    assert flags[other_class] == []


async def test_option_rows_are_not_open_market_trades(monkeypatch: pytest.MonkeyPatch) -> None:
    """A P/S row in Table II is an option contract bought or written, not the stock (CRWV's
    'call option (obligation to sell)' rows once ranked it #1 at $72.7B of 'sales')."""
    import dataclasses

    monkeypatch.setattr(tickers_router, "_today", lambda: date(2026, 9, 24))
    doc = parse_ownership((FIX / f"{SNOW}.xml").read_bytes())
    as_option = dataclasses.replace(doc, rows=tuple(
        dataclasses.replace(r, table="derivative") if r.code == "S" else r for r in doc.rows))
    async with session_scope() as session:
        await load_ownership(session, _meta(SNOW, date(2026, 9, 10)), as_option)
        flags = (await session.execute(text(
            "SELECT is_open_market, is_derivative FROM transactions WHERE code = 'S'"))).one()
    assert tuple(flags) == (False, True)
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        index = (await c.get("/api/tickers", params={"days": 30})).json()
    assert all(r["security"]["ticker"] != "SNOW" for r in index["items"])


async def test_the_same_trade_in_two_filings_counts_once(client: httpx.AsyncClient) -> None:
    """A fund and its general partner file separately for one trade (ETRA, Blackstone, Medline)."""
    doc = parse_ownership((FIX / f"{SNOW}.xml").read_bytes())
    sibling = "0001821737-26-999999"  # the same document filed again under another accession
    async with session_scope() as session:
        await load_ownership(session, _meta(sibling, date(2026, 9, 11)), doc)
    ctx = await _run_job()
    assert ctx.stats["duplicates"] >= 1
    index = (await client.get("/api/tickers", params={"days": 30})).json()
    snow = next(r for r in index["items"] if r["security"]["ticker"] == "SNOW")
    assert snow["sells"] == 1
    assert Decimal(snow["sell_value"]) == Decimal("16786000.00")
    # The person still sees it once on their own page (the counted copy lists them too).
    async with session_scope() as session:
        slug = (await session.execute(text(
            "SELECT e.slug FROM entities e JOIN entity_ids x ON x.entity_id = e.id WHERE x.value = '0001821737'"
        ))).scalar_one()
    stats = (await client.get(f"/api/people/{slug}")).json()["stats"]
    assert stats["open_market_sells"] == 1
    # Running again changes nothing.
    again = await _run_job()
    assert again.stats["duplicates_changed"] == 0


async def test_price_per_ads_on_ordinary_shares_is_flagged(client: httpx.AsyncClient) -> None:
    """SVRE: 2,501,582,400 ordinary shares x the $3.45 ADS price = $8.6B (one ADS is 43,200 shares)."""
    tx_id = await _snow_sale_id()
    async with session_scope() as session:
        filing_id = (await session.execute(text("SELECT filing_id FROM transactions WHERE id = :id"),
                                           {"id": tx_id})).scalar_one()
        await session.execute(text(
            "UPDATE transactions SET security_title = 'Ordinary Shares', footnote_ids = '{F9}' WHERE id = :id"),
            {"id": tx_id})
        await session.execute(text(
            "UPDATE filings SET footnotes = footnotes || jsonb_build_object('F9', CAST(:note AS text)) WHERE id = :f"),
            {"f": filing_id, "note": 'The price reported is the price per American Depositary Share ("ADS"). '
                                     "Each ADS represents 43,200 ordinary shares of the Issuer."})
    await _run_job()
    trade = (await client.get(f"/api/trades/{tx_id}")).json()["trade"]
    assert trade["data_issues"] == [VALUE_IMPLAUSIBLE]


async def _load_as(accession: str, filed: date, cik: str, name: str, nature: str) -> None:
    """The SNOW filing, re-filed under another accession by a different owner with a given D/I nature."""
    import dataclasses

    doc = parse_ownership((FIX / f"{SNOW}.xml").read_bytes())
    owner = dataclasses.replace(doc.owners[0], cik=cik, name=name)
    rows = tuple(dataclasses.replace(r, direct_indirect=nature) for r in doc.rows)
    async with session_scope() as session:
        await load_ownership(session, _meta(accession, filed), dataclasses.replace(doc, owners=(owner,), rows=rows))


async def test_two_unrelated_direct_buyers_are_not_merged(client: httpx.AsyncClient) -> None:
    # Identical direct trades by different people (like identical IPO allotments) are two trades.
    async with session_scope() as session:  # make the fixture's own rows direct too
        await session.execute(text("UPDATE transactions SET ownership_nature = 'D'"))
    await _load_as("0009999999-26-000001", date(2026, 9, 10), "0009999999", "Other Person", "D")
    await _run_job()
    async with session_scope() as session:
        dups = (await session.execute(text("SELECT count(*) FROM transactions WHERE duplicate_of_id IS NOT NULL"))).scalar_one()
    assert dups == 0


async def test_a_fund_and_its_general_partner_count_once(client: httpx.AsyncClient) -> None:
    # The general partner reports the fund's trade as indirect ownership, filed a day later.
    await _load_as("0009999998-26-000001", date(2026, 9, 11), "0009999998", "Example GP LLC", "I")
    async with session_scope() as session:  # marked at load time, no job needed
        dup_owner = (await session.execute(text(
            "SELECT e.name FROM transactions t JOIN entities e ON e.id = t.entity_id "
            "WHERE t.duplicate_of_id IS NOT NULL AND t.code = 'S'"))).scalar_one()
    assert dup_owner == "Example GP LLC"


async def test_price_per_ads_is_flagged_as_the_filing_loads() -> None:
    import dataclasses

    doc = parse_ownership((FIX / f"{SNOW}.xml").read_bytes())
    notes = {**doc.footnotes, "F9": "The price reported is the price per American Depositary Share (ADS). "
                                    "Each ADS represents 43,200 ordinary shares of the Issuer."}
    rows = tuple(dataclasses.replace(r, security_title="Ordinary Shares", footnote_ids=(*r.footnote_ids, "F9"))
                 if r.code == "S" else r for r in doc.rows)
    async with session_scope() as session:
        await load_ownership(session, _meta(SNOW, date(2026, 9, 10)), dataclasses.replace(doc, rows=rows, footnotes=notes))
        tx = (await session.execute(text("SELECT data_issues FROM transactions WHERE code = 'S'"))).scalar_one()
        event = (await session.execute(text(
            "SELECT e.size_usd FROM feed_events e JOIN transactions t ON t.id = e.transaction_id "
            "WHERE t.code = 'S'"))).scalar_one()
    assert list(tx) == [VALUE_IMPLAUSIBLE]
    assert event is None  # a doubtful value is not the event's size


def _dell_with(change: dict[int, dict[str, object]]) -> object:
    """The DELL filing with some of its Table I rows changed ({line_no: field values}).
    Lines 2-17 are sales of Class C stock at about $580."""
    import dataclasses

    doc = parse_ownership((FIX / f"{DELL}.xml").read_bytes())
    return dataclasses.replace(doc, rows=tuple(
        dataclasses.replace(r, **change[r.line_no])  # type: ignore[arg-type]
        if r.kind != "holding" and r.table == "nonderivative" and r.line_no in change else r
        for r in doc.rows))


async def _issues_by_line() -> dict[tuple[str, int], list[str]]:
    async with session_scope() as session:
        rows = (await session.execute(text(
            "SELECT t.table_name, t.line_no, t.data_issues FROM transactions t JOIN filings f ON f.id = t.filing_id "
            "WHERE f.source_key = :acc"), {"acc": DELL})).all()
    return {(r.table_name, r.line_no): list(r.data_issues) for r in rows}


async def test_a_misplaced_decimal_is_flagged_at_load_and_stays_flagged_on_reload() -> None:
    doc = _dell_with({2: {"price": Decimal("57760")}})  # $577.60 with the decimal point lost
    for _ in range(2):  # the second load is a reprocess of the same filing
        async with session_scope() as session:
            await load_ownership(session, _meta(DELL, date(2026, 9, 21)), doc)  # type: ignore[arg-type]
        flags = await _issues_by_line()
        assert flags[("nonderivative", 2)] == [VALUE_IMPLAUSIBLE]
        assert flags[("nonderivative", 3)] == []


async def test_an_option_priced_like_a_total_premium_is_flagged() -> None:
    """21,943 contracts at "$438,860" = $9.6B: the total premium typed as the price per contract."""
    doc = _dell_with({
        2: {"table": "derivative", "security_title": "Call Option (obligation to sell)", "price": Decimal("438860")},
        3: {"table": "derivative", "security_title": "Stock Option (right to buy)", "price": Decimal("12.50")},
        # A convertible note is priced per $1,000 of principal: legitimately above the share price.
        4: {"table": "derivative", "security_title": "3.0% Convertible Senior Notes", "price": Decimal("1250")},
    })
    async with session_scope() as session:
        await load_ownership(session, _meta(DELL, date(2026, 9, 21)), doc)  # type: ignore[arg-type]
    flags = await _issues_by_line()
    assert flags[("derivative", 2)] == [VALUE_IMPLAUSIBLE]
    assert flags[("derivative", 3)] == []
    assert flags[("derivative", 4)] == []


async def test_a_derivative_worth_far_more_than_its_underlying_shares_is_flagged() -> None:
    """Barfresh: a note convertible into 10,345 shares at $2.90 reported as 9,375 notes at $30,000."""
    note = {"table": "derivative", "security_title": "10% Series A convertible note", "shares": Decimal("9375"),
            "price": Decimal("30000"), "underlying_shares": Decimal("10345"),
            "conversion_or_exercise_price": Decimal("2.90")}
    prefunded = {"table": "derivative", "security_title": "Pre-Funded Warrants", "shares": Decimal("9078529"),
                 "price": Decimal("22.029"), "underlying_shares": Decimal("9078529"),
                 "conversion_or_exercise_price": Decimal("0.001")}
    # Every other sale becomes a derivative too, so the company has no share-price yardstick and the
    # rule falls back on each row's own exercise price (ignored when nominal).
    others = {n: {"table": "derivative", "security_title": "Stock Option (right to buy)"} for n in range(4, 18)}
    doc = _dell_with({2: note, 3: prefunded, **others})
    async with session_scope() as session:
        await load_ownership(session, _meta(DELL, date(2026, 9, 21)), doc)  # type: ignore[arg-type]
    flags = await _issues_by_line()
    assert flags[("derivative", 2)] == [VALUE_IMPLAUSIBLE]
    assert flags[("derivative", 3)] == []


async def test_totals_typed_as_the_price_on_every_row_are_flagged() -> None:
    """InnSuites: 34,800 shares at "$52,316.70", 25,000 at "$33,000"... (price = shares x $1.40)."""
    doc = _dell_with({n: {"shares": Decimal(n * 1000 + 700), "price": Decimal("1.40") * Decimal(n * 1000 + 700)}
                      for n in range(2, 18)})
    async with session_scope() as session:
        await load_ownership(session, _meta(DELL, date(2026, 9, 21)), doc)  # type: ignore[arg-type]
    flags = await _issues_by_line()
    assert all(flags[("nonderivative", n)] == [VALUE_IMPLAUSIBLE] for n in range(2, 18))


async def test_real_prices_whose_sizes_vary_are_not_totals() -> None:
    # Ordinary sales: sizes vary a lot, prices barely move (the DELL fixture as filed).
    async with session_scope() as session:
        await load_ownership(session, _meta(DELL, date(2026, 9, 21)),
                             parse_ownership((FIX / f"{DELL}.xml").read_bytes()))
    assert all(v == [] for k, v in (await _issues_by_line()).items() if k[0] == "nonderivative")


async def test_a_price_far_above_the_companys_form_144_price_is_flagged() -> None:
    """A company's Form 144 notices give an outside yardstick when its own rows can't."""
    import dataclasses

    from smt.ingest.form144 import load_form144
    from smt.sources.forms.form144 import parse_form144

    notice = parse_form144((FIX.parent / "form144" / "0001959173-26-006819.xml").read_bytes())
    doc = parse_ownership((FIX / f"{SNOW}.xml").read_bytes())
    total_as_price = dataclasses.replace(doc, rows=tuple(
        dataclasses.replace(r, price=r.price * 50_000) if r.code == "S" and r.price else r for r in doc.rows))
    async with session_scope() as session:
        await load_form144(session, _meta("0001959173-26-006819", date(2026, 9, 9)), notice)
        await load_ownership(session, _meta(SNOW, date(2026, 9, 10)), total_as_price)
        flags = (await session.execute(text("SELECT data_issues FROM transactions WHERE code = 'S'"))).scalar_one()
    assert list(flags) == [VALUE_IMPLAUSIBLE]


async def test_the_recompute_clears_a_flag_no_rule_supports_any_more(client: httpx.AsyncClient) -> None:
    from smt.ingest.quality import recompute_value_flags

    tx_id = await _snow_sale_id()
    async with session_scope() as session:  # set by an older, since corrected rule
        await session.execute(text("UPDATE transactions SET data_issues = ARRAY['value_implausible'] WHERE id = :id"),
                              {"id": tx_id})
    async with session_scope() as session:
        counts = await recompute_value_flags(session)
        issues = (await session.execute(text("SELECT data_issues FROM transactions WHERE id = :id"),
                                        {"id": tx_id})).scalar_one()
    assert counts["cleared"] == 1
    assert list(issues) == []


async def test_a_currency_flag_set_at_load_survives_the_recompute() -> None:
    """Cosan's price note 'Brazilian reais.': flagged at load, and the 30-minute job must keep it."""
    import dataclasses

    from smt.ingest.quality import PRICE_NOT_USD, recompute_value_flags

    doc = parse_ownership((FIX / f"{SNOW}.xml").read_bytes())
    notes = {**doc.footnotes, "F9": "Brazilian reais."}
    rows = tuple(dataclasses.replace(r, footnote_ids=(*r.footnote_ids, "F9")) if r.code == "S" else r
                 for r in doc.rows)
    async with session_scope() as session:
        await load_ownership(session, _meta(SNOW, date(2026, 9, 10)), dataclasses.replace(doc, rows=rows, footnotes=notes))
        await recompute_value_flags(session)
        issues = (await session.execute(text("SELECT data_issues FROM transactions WHERE code = 'S'"))).scalar_one()
    assert list(issues) == [VALUE_IMPLAUSIBLE, PRICE_NOT_USD]


async def test_a_trade_worth_many_times_the_companys_float_is_flagged() -> None:
    """Reborn Coffee: 131,387 shares at "$180,000" = $23.6 billion, for a company with a $10.9M float.

    The float is filer-typed and sometimes in thousands (LandBridge filed "1,700,000"), so it only
    judges prices of $1,000 and up: a normal price against a tiny float stays counted.
    """
    import dataclasses

    from smt.ingest.quality import recompute_value_flags

    doc = parse_ownership((FIX / f"{SNOW}.xml").read_bytes())  # one sale of 50,000 at $335.72 = $16.8M
    async with session_scope() as session:
        await load_ownership(session, _meta(SNOW, date(2026, 9, 10)), doc)
        issuer = (await session.execute(text("SELECT issuer_entity_id FROM transactions WHERE code = 'S'"))).scalar_one()
        # A float of $1M (or "in thousands", standing for $1 billion). The $335.72 sale is 16.8x it.
        await session.execute(text(
            "INSERT INTO issuer_public_float (entity_id, cik, http_status, public_float, as_of) "
            "VALUES (:e, '0001640147', 200, :f, '2026-06-30')"), {"e": issuer, "f": 1_000_000})
        await recompute_value_flags(session)
        normal_price = (await session.execute(text("SELECT data_issues FROM transactions WHERE code = 'S'"))).scalar_one()
    # A "price" of $2,000 on the same sale: $100M, 100 times a float of $1M. No other check sees it
    # (one row, no Form 144, no history), so the float must.
    rows = tuple(dataclasses.replace(r, price=Decimal("2000")) if r.code == "S" else r for r in doc.rows)
    async with session_scope() as session:
        await load_ownership(session, _meta(SNOW, date(2026, 9, 10)), dataclasses.replace(doc, rows=rows))
        await recompute_value_flags(session)
        flagged = (await session.execute(text("SELECT data_issues FROM transactions WHERE code = 'S'"))).scalar_one()
        # A float of zero (new listings report one) says nothing.
        await session.execute(text("UPDATE issuer_public_float SET public_float = 0"))
        await recompute_value_flags(session)
        zero = (await session.execute(text("SELECT data_issues FROM transactions WHERE code = 'S'"))).scalar_one()
    assert list(normal_price) == []
    assert list(flagged) == [VALUE_IMPLAUSIBLE]
    assert list(zero) == []


async def test_a_price_a_thousand_times_the_classs_own_history_is_flagged() -> None:
    """Securetech: its Series A Preferred changed hands at $1.00 in May, then a 'buy' at $100,000."""
    import dataclasses

    from smt.ingest.quality import recompute_value_flags

    doc = parse_ownership((FIX / f"{DELL}.xml").read_bytes())
    history = {2: {"code": "D", "price": Decimal("1.00")}, 3: {"code": "D", "price": Decimal("1.00")},
               4: {"price": Decimal("100000"), "code": "P", "acquired_disposed": "A"}}
    rows = []
    for r in doc.rows:
        if r.kind != "holding" and r.table == "nonderivative" and r.line_no in history:
            change = history[r.line_no]
            day = r.transaction_date if r.line_no == 4 else date(2026, 5, 20)
            rows.append(dataclasses.replace(r, security_title="Series A Preferred Stock", transaction_date=day,
                                            **change))  # type: ignore[arg-type]
        else:
            rows.append(r)
    async with session_scope() as session:
        await load_ownership(session, _meta(DELL, date(2026, 9, 21)), dataclasses.replace(doc, rows=tuple(rows)))
        await recompute_value_flags(session)
    flags = await _issues_by_line()
    assert flags[("nonderivative", 4)] == [VALUE_IMPLAUSIBLE]
    assert flags[("nonderivative", 5)] == []  # ordinary sales of the common stock are untouched


async def test_an_ads_ratio_alone_is_judged_against_the_classs_own_price_per_share() -> None:
    """Kanzhun states only 'Each ADS represents two Class A ordinary shares': its $8.28 rows are
    per share (its other rows trade at $7.25), so they count. Mesoblast's $14.10 against $1.43 a
    share, 10 per ADS, is an ADS price and is flagged. Built on DELL's sales at about $580."""
    import dataclasses

    from smt.ingest.quality import recompute_value_flags

    title = "Class A ordinary shares"
    held = 'These Class A ordinary shares are held through American Depositary Shares ("ADS").'

    def version(accession_tail: str, ratio_note: str | None, price: Decimal | None) -> tuple[str, object]:
        doc = parse_ownership((FIX / f"{DELL}.xml").read_bytes())
        notes = dict(doc.footnotes)
        extra: tuple[str, ...] = ()
        if ratio_note:
            notes.update({"F8": held, "F9": ratio_note})
            extra = ("F8", "F9")
        rows = tuple(
            dataclasses.replace(r, security_title=title, footnote_ids=(*r.footnote_ids, *extra),
                                price=price if price is not None else r.price)
            if r.kind != "holding" and r.table == "nonderivative" and r.code == "S" else r
            for r in doc.rows)
        return f"0000000000-26-{accession_tail}", dataclasses.replace(doc, rows=rows, footnotes=notes)

    loads = [version("000001", None, None),                                                    # the yardstick
             version("000002", "Each ADS represents two Class A ordinary shares.", Decimal("600")),
             version("000003", "Each ADS represents ten Class A ordinary shares.", Decimal("5800"))]
    async with session_scope() as session:
        for accession, doc in loads:
            await load_ownership(session, _meta(accession, date(2026, 9, 21)), doc)  # type: ignore[arg-type]

    async def issues_by_filing() -> dict[str, set[tuple[str, ...]]]:
        async with session_scope() as session:
            rows = (await session.execute(text(
                "SELECT f.source_key, t.data_issues FROM transactions t JOIN filings f ON f.id = t.filing_id "
                "WHERE t.code = 'S'"))).all()
        out: dict[str, set[tuple[str, ...]]] = {}
        for key, found in rows:
            out.setdefault(key, set()).add(tuple(found))
        return out

    expected = {"0000000000-26-000001": {()}, "0000000000-26-000002": {()},
                "0000000000-26-000003": {(VALUE_IMPLAUSIBLE,)}}
    assert await issues_by_filing() == expected          # as the filings load
    async with session_scope() as session:
        await recompute_value_flags(session)
    assert await issues_by_filing() == expected          # and after the 30-minute recompute


@pytest.mark.parametrize(("close_after_days", "recorded_after_days", "splits_read_after_days", "split_after_days",
                          "flagged"), [
    (0, 1, None, None, True),      # recorded the day after its session: no later split can have changed it
    (0, 400, None, None, False),   # recorded long after, splits unknown: it may be split-adjusted
    (0, 400, 400, None, True),     # splits read with the close, none since the session: as traded
    (0, 400, 400, 30, False),      # a split between the session and the fetch: the close was re-based
    (0, 400, 399, None, False),    # splits read before the close was fetched don't vouch for it
    (0, 400, 400, 500, True),      # a split after the fetch can't have changed it
    (30, 31, 31, None, True),      # a close a month after the trade, as traded
    (30, 31, 31, 10, False),       # ...but after a split the trade predates (BKNG, 25-for-1 on 6 April)
])
async def test_a_stored_close_judges_prices_only_while_it_is_as_traded(
    close_after_days: int, recorded_after_days: int, splits_read_after_days: int | None,
    split_after_days: int | None, flagged: bool,
) -> None:
    """Closes are split-adjusted when fetched: one fetched after a split no longer shows that
    day's price (CRWD $684 filed, $168 stored after a 4-for-1), so it judges a price only when
    it was recorded soon after its session or its provider's splits show none in between."""
    from smt.ingest.quality import recompute_value_flags

    # Every DELL sale "at $40,000" against a close of $580: only the close can see it (the class's
    # own median is $40,000 too, and there's no Form 144, float or history).
    doc = _dell_with({line: {"price": Decimal("40000")} for line in range(2, 18)})
    async with session_scope() as session:
        await load_ownership(session, _meta(DELL, date(2026, 9, 21)), doc)  # type: ignore[arg-type]
        security_id, traded_on = (await session.execute(text(
            "SELECT security_id, traded_on FROM transactions WHERE code = 'S' LIMIT 1"))).one()
        await session.execute(text(
            "INSERT INTO prices_daily (security_id, day, provider, close, fetched_at) "
            "VALUES (:s, CAST(:d AS date) + CAST(:day AS int), 'finlake', 580, CAST(:d AS date) + CAST(:after AS int))"),
            {"s": security_id, "d": traded_on, "day": close_after_days, "after": recorded_after_days})
        if splits_read_after_days is not None:
            await session.execute(text(
                "INSERT INTO price_split_checks (security_id, provider, checked_at) "
                "VALUES (:s, 'finlake', CAST(:d AS date) + CAST(:after AS int))"),
                {"s": security_id, "d": traded_on, "after": splits_read_after_days})
        if split_after_days is not None:
            await session.execute(text(
                "INSERT INTO price_splits (security_id, provider, ex_date, ratio) "
                "VALUES (:s, 'finlake', CAST(:d AS date) + CAST(:after AS int), 4)"),
                {"s": security_id, "d": traded_on, "after": split_after_days})
        await recompute_value_flags(session)
        issues = {tuple(r) for r in (await session.execute(text(
            "SELECT data_issues FROM transactions WHERE code = 'S'"))).scalars()}
    assert issues == ({(VALUE_IMPLAUSIBLE,)} if flagged else {()})


def _dell_as_ordinary_shares(prices: dict[int, Decimal], ratio_note: str | None) -> object:
    """DELL's 16 sales retitled 'Ordinary Shares' (at about $580), some repriced, with the filing
    stating an ADS ratio in a note no row points at (as Alibaba's RSU notes do)."""
    import dataclasses

    doc = parse_ownership((FIX / f"{DELL}.xml").read_bytes())
    notes = {**doc.footnotes, "F9": ratio_note} if ratio_note else doc.footnotes
    rows = tuple(
        dataclasses.replace(r, security_title="Ordinary Shares", price=prices.get(r.line_no, r.price))
        if r.kind != "holding" and r.table == "nonderivative" and r.code == "S" else r
        for r in doc.rows)
    return dataclasses.replace(doc, rows=rows, footnotes=notes)


@pytest.mark.parametrize(("ratio_note", "flagged"), [("Each ADS represents 8 ordinary shares.", True),
                                                     ("Each ADS represents one ordinary share.", False),
                                                     (None, False)])
async def test_an_ads_price_on_ordinary_shares_is_flagged_from_the_companys_ratio(
    ratio_note: str | None, flagged: bool,
) -> None:
    """Alibaba 330864: 'Ordinary Shares' at $94.92, one ADS (8 shares), against $12-16 a share, with
    no ADS word in the row's own notes; the ratio is in the company's filings. Here one DELL sale
    at 8 x $580 among 15 at about $580: flagged at load, and the recompute agrees (nothing to change)."""
    from smt.ingest.quality import compute_value_flags, recompute_value_flags

    doc = _dell_as_ordinary_shares({2: Decimal("4640")}, ratio_note)
    async with session_scope() as session:
        await load_ownership(session, _meta(DELL, date(2026, 9, 21)), doc)  # type: ignore[arg-type]
    at_load = await _issues_by_line()
    async with session_scope() as session:
        changes = await compute_value_flags(session)
        await recompute_value_flags(session)
        size = (await session.execute(text(
            "SELECT e.size_usd FROM feed_events e JOIN transactions t ON t.id = e.transaction_id "
            "WHERE t.table_name = 'nonderivative' AND t.line_no = 2"))).scalar_one()
    assert at_load[("nonderivative", 2)] == ([VALUE_IMPLAUSIBLE] if flagged else [])
    assert all(at_load[("nonderivative", line)] == [] for line in range(3, 18))
    assert changes == []                       # load time and the recompute agree
    assert await _issues_by_line() == at_load
    assert (size is None) is flagged           # a flagged value is no feed event's size


async def test_open_market_rows_with_no_size_have_no_value() -> None:
    """CLMT tx 332672: a sale of 24,555 at 0 read as a '-$0' sale; Aetos' funds file LLC interests
    as 0 shares at $7,333.34. Neither states a size: the value is missing (never $0), at load and,
    for rows loaded before, after the 30-minute job, with the feed event in step."""
    doc = _dell_with({2: {"price": Decimal("0")}, 3: {"shares": Decimal("0")}})
    async with session_scope() as session:
        await load_ownership(session, _meta(DELL, date(2026, 9, 21)), doc)  # type: ignore[arg-type]

    async def values() -> dict[int, tuple[object, object, object, list[str]]]:
        async with session_scope() as session:
            rows = (await session.execute(text(
                "SELECT t.line_no, t.value, e.size_usd, e.payload->'value' AS shown, t.data_issues "
                "FROM transactions t JOIN feed_events e ON e.transaction_id = t.id "
                "WHERE t.table_name = 'nonderivative' AND t.line_no IN (2, 3, 4)"))).all()
        return {r.line_no: (r.value, r.size_usd, r.shown, list(r.data_issues)) for r in rows}

    at_load = await values()
    assert at_load[2] == (None, None, None, [])                    # price 0: no value, not flagged
    assert at_load[3] == (None, None, None, [VALUE_IMPLAUSIBLE])   # 0 shares at a price: check the filing
    assert at_load[4][0] is not None                               # an ordinary sale keeps its value
    assert at_load[4][1] is not None

    # As loaded before this rule: $0 values on the rows and their feed events.
    async with session_scope() as session:
        await session.execute(text(
            "UPDATE transactions SET value = 0 WHERE table_name = 'nonderivative' AND line_no IN (2, 3)"))
        await session.execute(text(
            "UPDATE feed_events e SET size_usd = 0, payload = jsonb_set(e.payload, '{value}', '\"0.00\"') "
            "FROM transactions t WHERE e.transaction_id = t.id AND t.table_name = 'nonderivative' "
            "AND t.line_no IN (2, 3)"))
    first = await _run_job()
    assert await values() == at_load
    assert first.stats["zero_values_cleared"] == 2
    assert (await _run_job()).stats["zero_values_cleared"] == 0    # idempotent


async def test_the_recompute_writes_only_rows_that_still_have_the_flags_it_read() -> None:
    """The 30-minute job works out the flags reading only, then writes the differences. A row whose
    flags changed in between (its filing reloaded by a live poll) keeps the load's flags until the
    next run; the others are written."""
    from smt.ingest.quality import apply_value_flags, compute_value_flags

    async with session_scope() as session:
        await load_ownership(session, _meta(DELL, date(2026, 9, 21)), _dell_with({}))  # type: ignore[arg-type]
        await session.execute(text(  # two rows carrying a flag no rule supports
            "UPDATE transactions SET data_issues = ARRAY['value_implausible'] "
            "WHERE table_name = 'nonderivative' AND line_no IN (2, 3)"))
    async with session_scope() as session:
        changes = await compute_value_flags(session)
        await session.rollback()
    assert sorted(c.after for c in changes) == [[], []]
    async with session_scope() as session:  # a reload in between sets line 3's flags again
        await session.execute(text(
            "UPDATE transactions SET data_issues = ARRAY['value_implausible', 'date_after_filing'] "
            "WHERE table_name = 'nonderivative' AND line_no = 3"))
    async with session_scope() as session:
        counts = await apply_value_flags(session, changes)
    flags = await _issues_by_line()
    assert (counts["changed"], counts["skipped"], counts["cleared"]) == (1, 1, 1)
    assert flags[("nonderivative", 2)] == []
    assert flags[("nonderivative", 3)] == [VALUE_IMPLAUSIBLE, DATE_AFTER_FILING]
    async with session_scope() as session:
        assert await compute_value_flags(session) != []  # the next run sees line 3 again


async def test_the_recompute_skips_a_row_reloaded_with_new_figures_and_the_same_flags() -> None:
    """A reload between the compute and the apply can fix a row's figures and leave it the same
    flags as before (none). DELL line 2 is stored at $57,760 (the decimal point lost: $577.60, as
    in a real Ducommun filing's $15,159 for $151.59) with no flag yet, so the recompute works out
    value_implausible for it; then the filing is reprocessed with the price as filed, which flags
    nothing. The flag worked out from the old price must not land on the corrected row: its
    version (xmin) changed, so the apply skips it."""
    from smt.ingest.quality import apply_value_flags, compute_value_flags

    doc = _dell_with({})
    async with session_scope() as session:
        await load_ownership(session, _meta(DELL, date(2026, 9, 21)), doc)  # type: ignore[arg-type]
        await session.execute(text(  # as if loaded before the rules: the price 100 times too high, no flag
            "UPDATE transactions SET price = price * 100, value = value * 100 "
            "WHERE table_name = 'nonderivative' AND line_no = 2"))
    async with session_scope() as session:
        changes = await compute_value_flags(session)
        await session.rollback()
    assert [(c.before, c.after) for c in changes] == [([], [VALUE_IMPLAUSIBLE])]
    async with session_scope() as session:  # the reprocess in between: the price as filed, no flags
        await load_ownership(session, _meta(DELL, date(2026, 9, 21)), doc)  # type: ignore[arg-type]
    assert (await _issues_by_line())[("nonderivative", 2)] == []
    async with session_scope() as session:
        counts = await apply_value_flags(session, changes)
    assert (counts["changed"], counts["skipped"], counts["newly_flagged"]) == (0, 1, 0)
    assert (await _issues_by_line())[("nonderivative", 2)] == []
    async with session_scope() as session:
        assert await compute_value_flags(session) == []  # and the next run agrees


async def test_the_recompute_writes_a_row_nothing_touched_since() -> None:
    """The version guard skips only rows written in between: with no reload, the same flag lands."""
    from smt.ingest.quality import apply_value_flags, compute_value_flags

    async with session_scope() as session:
        await load_ownership(session, _meta(DELL, date(2026, 9, 21)), _dell_with({}))  # type: ignore[arg-type]
        await session.execute(text(
            "UPDATE transactions SET price = price * 100, value = value * 100 "
            "WHERE table_name = 'nonderivative' AND line_no = 2"))
    async with session_scope() as session:
        changes = await compute_value_flags(session)
        await session.rollback()
    async with session_scope() as session:
        counts = await apply_value_flags(session, changes)
    assert (counts["changed"], counts["skipped"], counts["newly_flagged"]) == (1, 0, 1)
    assert (await _issues_by_line())[("nonderivative", 2)] == [VALUE_IMPLAUSIBLE]
