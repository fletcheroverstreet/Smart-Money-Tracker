"""GET /api/tickers: companies ranked by open-market insider trading, over real fixture filings.

Every fixture is a real EDGAR document (tests/fixtures/edgar/ownership and
insider_dataset), loaded through the real loader. The numbers expected below
were read from the XML by hand:

* DELL  0001193125-26-396718  16 sales (S) + 2 conversions (M), 2026-09-17, one joint filing (5 owners)
* LUNR  0001213900-26-098884  3 sales (S) on 2026-09-08/09, plus M and D rows
* SNOW  0001821737-26-000013  1 sale (S) of 50,000 sh at $335.72 on 2026-09-09, plus F and G rows
* SEPN  0001193125-26-352692  2 sales (S) on 2026-08-12
* COCO  0001482981-26-000122  2 sales (S) on 2026-04-28/29
* GHM   0000891839-25-000050  1 purchase (P) of 1,200 sh at $49.01 on 2025-09-12,
        corrected by the 4/A 0000891839-26-000409 (same purchase, so it must count once)
* UMC   0001193125-26-301380, -393652, -397195: 4 sales (S) on 2026-07-13, 09-16, 09-17 and
        09-22, every price in New Taiwan dollars (footnote F1 "New Taiwan Dollars"). Loaded
        only by the test that needs them.

"Today" is pinned (the window is relative to it), so the tests don't depend on
the date they run.
"""

from __future__ import annotations

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
from smt.sources.forms.ownership import PARSER_VERSION, parse_ownership

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]
FIX = Path(__file__).resolve().parents[1] / "fixtures" / "edgar"

TODAY = date(2026, 9, 24)

FILINGS: list[tuple[str, str, str, date]] = [
    ("ownership", "0001193125-26-396718", "4", date(2026, 9, 21)),        # DELL
    ("ownership", "0001213900-26-098884", "4", date(2026, 9, 10)),        # LUNR
    ("ownership", "0001821737-26-000013", "4", date(2026, 9, 10)),        # SNOW
    ("ownership", "0001193125-26-352692", "4", date(2026, 8, 14)),        # SEPN
    ("insider_dataset", "0001482981-26-000122", "4", date(2026, 4, 30)),  # COCO
    ("ownership", "0000891839-25-000050", "4", date(2025, 9, 15)),        # GHM original
    ("ownership", "0000891839-26-000409", "4/A", date(2026, 9, 21)),      # GHM amendment
]


def _meta(accession: str, form: str, filed: date) -> FilingMeta:
    return FilingMeta(
        accession=accession, form_type=form, filed_on=filed,
        accepted_at=datetime(filed.year, filed.month, filed.day, 21, 0, tzinfo=UTC),
        url=f"https://www.sec.gov/Archives/edgar/data/0/{accession.replace('-', '')}/{accession}-index.htm",
        primary_doc_url=None, detected_via="manual", parser_version=PARSER_VERSION, live=False,
    )


@pytest.fixture
def today(monkeypatch: pytest.MonkeyPatch) -> date:
    """Pin the router's "today" (US Eastern) so the windows are fixed."""
    monkeypatch.setattr(tickers_router, "_today", lambda: TODAY)
    return TODAY


@pytest.fixture
async def client(today: date) -> AsyncIterator[httpx.AsyncClient]:
    async with session_scope() as session:
        for folder, accession, form, filed in FILINGS:
            doc = parse_ownership((FIX / folder / f"{accession}.xml").read_bytes())
            await load_ownership(session, _meta(accession, form, filed), doc)
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _index(client: httpx.AsyncClient, **params: Any) -> dict[str, Any]:
    response = await client.get("/api/tickers", params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _by_ticker(page: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["security"]["ticker"]: row for row in page["items"]}


async def _db_sum(ticker: str, code: str) -> Decimal:
    """The sum of current rows' values for one company and code, straight from the table."""
    async with session_scope() as session:
        total = (await session.execute(text(
            "SELECT sum(t.value) FROM transactions t JOIN securities s ON s.issuer_entity_id = t.issuer_entity_id "
            "WHERE s.primary_ticker = :t AND t.code = :c AND t.is_current"), {"t": ticker, "c": code})).scalar_one()
    assert total is not None
    return Decimal(total)


async def test_default_window_is_the_last_30_days(client: httpx.AsyncClient) -> None:
    page = await _index(client)
    assert page["days"] == 30
    assert page["until"] == "2026-09-24"
    assert page["since"] == "2026-08-26"   # 30 days including today
    # SEPN (Aug 12), COCO (April) and GHM (2025) traded before the window.
    assert set(_by_ticker(page)) == {"DELL", "LUNR", "SNOW"}


async def test_counts_only_open_market_trades_and_sums_only_their_values(client: httpx.AsyncClient) -> None:
    rows = _by_ticker(await _index(client))
    snow = rows["SNOW"]
    # One S row; the F (tax withholding) and G (gift) rows on the same filing are left out.
    assert (snow["buys"], snow["sells"]) == (0, 1)
    assert Decimal(snow["sell_value"]) == Decimal("16786000.00")   # 50,000 x $335.72
    assert snow["buy_value"] is None                               # no buys: missing, never $0
    assert Decimal(snow["net_value"]) == Decimal("-16786000.00")
    assert snow["last_traded_on"] == "2026-09-09"
    assert snow["security"]["name"] == "Snowflake Inc."
    assert snow["issuer"]["kind"] == "company"

    dell = rows["DELL"]
    # 16 sales; the two M (conversion) rows are not open-market trades.
    assert (dell["buys"], dell["sells"]) == (0, 16)
    assert Decimal(dell["sell_value"]) == await _db_sum("DELL", "S")
    assert (dell["trades_without_price"], dell["trades_left_out"]) == (0, 0)

    lunr = rows["LUNR"]
    assert (lunr["buys"], lunr["sells"]) == (0, 3)
    assert lunr["last_traded_on"] == "2026-09-09"


async def test_joint_filing_counts_one_insider(client: httpx.AsyncClient) -> None:
    # The Dell filing names five Silver Lake entities; its trades are attributed to one.
    assert _by_ticker(await _index(client))["DELL"]["insiders"] == 1


async def test_amended_trade_counts_once(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # Move "today" to just after the GHM purchase: the original and its 4/A are both
    # stored, but only the corrected (current) row counts.
    monkeypatch.setattr(tickers_router, "_today", lambda: date(2025, 10, 1))
    ghm = _by_ticker(await _index(client))["GHM"]
    assert (ghm["buys"], ghm["sells"]) == (1, 0)
    assert Decimal(ghm["buy_value"]) == Decimal("58812.00")   # 1,200 x $49.01
    assert ghm["sell_value"] is None
    assert Decimal(ghm["net_value"]) == Decimal("58812.00")


async def test_days_widens_the_window(client: httpx.AsyncClient) -> None:
    assert set(_by_ticker(await _index(client, days=90))) == {"DELL", "LUNR", "SNOW", "SEPN"}
    year = await _index(client, days=365)
    assert year["since"] == "2025-09-25"
    # COCO (April 2026) is in; the GHM purchase (2025-09-12) is just outside a year.
    assert set(_by_ticker(year)) == {"DELL", "LUNR", "SNOW", "SEPN", "COCO"}


async def test_future_dated_trades_are_outside_the_window(client: httpx.AsyncClient,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    # On 2026-09-08 the Sept 9 LUNR and SNOW trades haven't happened yet.
    monkeypatch.setattr(tickers_router, "_today", lambda: date(2026, 9, 8))
    rows = _by_ticker(await _index(client))
    assert "SNOW" not in rows
    assert "DELL" not in rows
    assert rows["LUNR"]["sells"] == 2   # the two Sept 8 sales; the Sept 9 one is "in the future"
    assert rows["LUNR"]["last_traded_on"] == "2026-09-08"


async def test_sorts(client: httpx.AsyncClient) -> None:
    activity = [r["security"]["ticker"] for r in (await _index(client, sort="activity"))["items"]]
    assert activity == ["DELL", "LUNR", "SNOW"]   # 16, 3 and 1 trades

    sells = (await _index(client, sort="sells"))["items"]
    values = [Decimal(r["sell_value"]) for r in sells]
    assert values == sorted(values, reverse=True)

    # Net: the smallest net selling comes first, the largest last.
    net = (await _index(client, sort="net"))["items"]
    nets = [Decimal(r["net_value"]) for r in net]
    assert nets == sorted(nets, reverse=True)
    assert net[-1]["security"]["ticker"] == sells[0]["security"]["ticker"]


async def test_buys_sort_puts_companies_with_purchases_first(client: httpx.AsyncClient,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tickers_router, "_today", lambda: date(2025, 10, 1))
    await _load_extra_sale_in_window()
    rows = (await _index(client, sort="buys"))["items"]
    assert rows[0]["security"]["ticker"] == "GHM"
    assert all(r["buy_value"] is None for r in rows[1:])


async def _load_extra_sale_in_window() -> None:
    """Move one fixture sale (a Dell row) to 2025-09-30, so a company with only sales is in the window."""
    async with session_scope() as session:
        await session.execute(text(
            "UPDATE transactions SET traded_on = DATE '2025-09-30' "
            "WHERE id = (SELECT min(id) FROM transactions WHERE code = 'S')"))


async def test_prefix_filter_on_ticker_or_name(client: httpx.AsyncClient) -> None:
    assert set(_by_ticker(await _index(client, q="sn"))) == {"SNOW"}
    assert set(_by_ticker(await _index(client, q="Snowfl"))) == {"SNOW"}
    assert set(_by_ticker(await _index(client, q="d"))) == {"DELL"}
    # LIKE wildcards in the query are literal characters, not wildcards.
    assert (await _index(client, q="%"))["items"] == []
    assert (await _index(client, q="_ELL"))["items"] == []


async def test_paging_has_no_gaps_or_repeats(client: httpx.AsyncClient) -> None:
    everything = [r["security"]["ticker"] for r in (await _index(client, days=365))["items"]]
    seen: list[str] = []
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"days": 365, "limit": 2} | ({"cursor": cursor} if cursor else {})
        page = await _index(client, **params)
        seen += [r["security"]["ticker"] for r in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert seen == everything
    assert len(seen) == len(set(seen)) == 5


async def test_companies_without_a_ticker_are_left_out(client: httpx.AsyncClient) -> None:
    async with session_scope() as session:
        await session.execute(text("UPDATE securities SET primary_ticker = NULL WHERE primary_ticker = 'LUNR'"))
    assert "LUNR" not in _by_ticker(await _index(client))
    assert len((await _index(client))["items"]) == 2


async def test_bad_parameters_are_422(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/tickers", params={"days": 0})).status_code == 422
    assert (await client.get("/api/tickers", params={"days": 366})).status_code == 422
    assert (await client.get("/api/tickers", params={"sort": "price"})).status_code == 422
    assert (await client.get("/api/tickers", params={"cursor": "junk"})).status_code == 400


async def test_the_index_does_not_shadow_ticker_pages(client: httpx.AsyncClient) -> None:
    snow = (await client.get("/api/tickers/SNOW")).json()
    assert snow["security"]["ticker"] == "SNOW"
    # The index row and the ticker page's trade table agree on the company's sales.
    trades = (await client.get("/api/tickers/DELL/insiders", params={"codes": "S"})).json()["items"]
    row = _by_ticker(await _index(client))["DELL"]
    assert row["sells"] == len(trades) == 16
    assert Decimal(row["sell_value"]) == sum(Decimal(t["value"]) for t in trades)


@pytest.mark.parametrize(("today_", "sepn_sells"), [(date(2026, 11, 9), 2), (date(2026, 11, 10), 0)])
async def test_the_ticker_page_uses_the_same_90_days_as_the_index(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, today_: date, sepn_sells: int,
) -> None:
    # SEPN's two sales are dated 2026-08-12: day 90 of the window on Nov 9, outside it on Nov 10.
    monkeypatch.setattr(tickers_router, "_today", lambda: today_)
    rows = _by_ticker(await _index(client, days=90))
    for symbol in ("DELL", "LUNR", "SNOW", "SEPN", "COCO", "GHM"):
        page = (await client.get(f"/api/tickers/{symbol}")).json()
        row = rows.get(symbol, {"buys": 0, "sells": 0})
        assert (page["open_market_buys_90d"], page["open_market_sells_90d"]) == (row["buys"], row["sells"]), symbol
    assert (await client.get("/api/tickers/SEPN")).json()["open_market_sells_90d"] == sepn_sells


async def test_a_company_in_capitals_reads_the_same_everywhere(client: httpx.AsyncClient) -> None:
    # EDGAR spells GHM's issuer "GRAHAM CORP"; every page that links the company shows "Graham Corp".
    async with session_scope() as session:
        tx_id, owner_slug = (await session.execute(text(
            "SELECT t.id, o.slug FROM transactions t JOIN entities o ON o.id = t.entity_id "
            "JOIN securities s ON s.id = t.security_id WHERE s.primary_ticker = 'GHM' AND t.is_current "
            "ORDER BY t.id LIMIT 1"))).one()
        await session.execute(text("UPDATE feed_events SET is_live = true"))   # the Live feed shows live events
    names = [(await client.get(f"/api/trades/{tx_id}")).json()["trade"]["issuer"]["name"],
             (await client.get(f"/api/people/{owner_slug}/trades")).json()["items"][0]["issuer"]["name"],
             (await client.get(f"/api/people/{owner_slug}/positions")).json()[0]["issuer"]["name"],
             (await client.get("/api/tickers/GHM")).json()["issuer"]["name"]]
    feed = (await client.get("/api/feed", params={"ticker": "GHM"})).json()["items"]
    names += [e["payload"]["issuer"]["name"] for e in feed]
    assert len(names) > 4
    assert set(names) == {"Graham Corp"}


#: UMC's Form 4s: four sales priced in New Taiwan dollars (footnote F1 on every price).
UMC_FILINGS: list[tuple[str, date]] = [
    ("0001193125-26-301380", date(2026, 7, 13)),   # 1,900,000 sh at NT$154.89
    ("0001193125-26-393652", date(2026, 9, 17)),   # 1,200,000 at NT$142.333 and 400,000 at NT$145
    ("0001193125-26-397195", date(2026, 9, 22)),   # 10,000 at NT$163
]


async def test_umc_prices_in_new_taiwan_dollars_are_left_out_not_missing(client: httpx.AsyncClient) -> None:
    """Round 6: /tickers?days=90&q=UMC read "Sold: 4 (no value reported)" although all four filings
    state a price. They are left out of the dollar sums because the price is in NT$, not because it
    is missing, and the API now says which."""
    async with session_scope() as session:
        for accession, filed in UMC_FILINGS:
            doc = parse_ownership((FIX / "ownership" / f"{accession}.xml").read_bytes())
            await load_ownership(session, _meta(accession, "4", filed), doc)
    umc = _by_ticker(await _index(client, days=90, q="UMC"))["UMC"]
    assert (umc["buys"], umc["sells"]) == (0, 4)
    assert umc["sell_value"] is None            # no dollar figure to add: a dash, never $0
    assert umc["net_value"] is None
    assert umc["trades_left_out"] == 4
    assert (umc["buys_left_out"], umc["sells_left_out"]) == (0, 4)
    assert umc["trades_without_price"] == 0     # every one of them states a price


async def test_a_trade_with_no_price_is_counted_as_without_price(client: httpx.AsyncClient) -> None:
    """A sale whose filing gives no price has no value: counted, not summed, and not 'left out'."""
    async with session_scope() as session:
        await session.execute(text(
            "UPDATE transactions SET price = NULL, value = NULL WHERE id = (SELECT min(t.id) FROM transactions t "
            "JOIN securities s ON s.id = t.security_id WHERE s.primary_ticker = 'LUNR' AND t.code = 'S')"))
    lunr = _by_ticker(await _index(client))["LUNR"]
    assert lunr["sells"] == 3
    assert (lunr["trades_without_price"], lunr["trades_left_out"], lunr["sells_left_out"]) == (1, 0, 0)
