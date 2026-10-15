"""The read-only API over real fixture data (loaded through the real loaders)."""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from smt.api.app import create_app
from smt.config import reset_settings_cache
from smt.db.session import session_scope
from smt.ingest.form144 import load_form144
from smt.ingest.ownership import FilingMeta, load_ownership
from smt.sources.forms.form144 import parse_form144
from smt.sources.forms.ownership import PARSER_VERSION, parse_ownership

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]
FIX = Path(__file__).resolve().parents[1] / "fixtures" / "edgar"


def _meta(acc: str, form: str, filed: date, accepted: datetime | None = None) -> FilingMeta:
    return FilingMeta(accession=acc, form_type=form, filed_on=filed, accepted_at=accepted,
                      url=f"https://www.sec.gov/x/{acc}-index.htm", primary_doc_url=f"https://www.sec.gov/x/{acc}.xml",
                      detected_via="feed", parser_version=PARSER_VERSION)


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """Load a few real filings, then serve the app in-process."""
    async with session_scope() as s:
        for acc, form, filed, accepted in [
            ("0001193125-26-396718", "4", date(2026, 9, 21), datetime(2026, 9, 21, 20, 30, 41, tzinfo=UTC)),
            ("0001821737-26-000013", "4", date(2026, 9, 10), datetime(2026, 9, 10, 21, 0, tzinfo=UTC)),
            ("0001592539-26-000001", "3", date(2026, 9, 21), None),
        ]:
            doc = parse_ownership((FIX / "ownership" / f"{acc}.xml").read_bytes())
            await load_ownership(s, _meta(acc, form, filed, accepted), doc)
        notice = parse_form144((FIX / "form144" / "0001959173-26-006819.xml").read_bytes())
        await load_form144(s, _meta("0001959173-26-006819", "144", date(2026, 9, 9)), notice)
        await s.execute(__import__("sqlalchemy").text(
            "INSERT INTO securities (name, primary_ticker, sector_source) VALUES ('Dell Technologies', 'DELLX', 'unmapped')"))
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_feed_defaults_to_open_market_trades(client: httpx.AsyncClient) -> None:
    page = (await client.get("/api/feed")).json()
    codes = {i["payload"].get("code") for i in page["items"] if i["event_type"] == "insider_trade"}
    assert codes <= {"P", "S"}
    assert codes  # the Dell and Snowflake sales are there
    everything = (await client.get("/api/feed", params={"codes": "all", "limit": 200})).json()
    assert len(everything["items"]) > len(page["items"])


async def test_feed_keyset_pagination_has_no_gaps_or_repeats(client: httpx.AsyncClient) -> None:
    seen: list[int] = []
    cursor = None
    while True:
        params = {"codes": "all", "limit": 7} | ({"cursor": cursor} if cursor else {})
        page = (await client.get("/api/feed", params=params)).json()
        seen += [i["id"] for i in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    everything = (await client.get("/api/feed", params={"codes": "all", "limit": 200})).json()
    assert seen == [i["id"] for i in everything["items"]]
    assert len(seen) == len(set(seen))


async def test_bad_cursor_is_a_400_not_a_500(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/feed", params={"cursor": "not-a-cursor"})).status_code == 400
    # Well-formed base64 JSON with the wrong contents must be a 400 too, never a 500.
    wrong = [
        ("/api/feed", ["notadate", 1]),
        ("/api/feed", ["2026-09-24T12:00:00", "x"]),
        ("/api/people/insiders", [-5]),
        ("/api/people/insiders", ["ten"]),
        ("/api/tickers", [True]),
    ]
    for path, values in wrong:
        token = base64.urlsafe_b64encode(json.dumps(values).encode()).decode().rstrip("=")
        response = await client.get(path, params={"cursor": token})
        assert response.status_code == 400, (path, values, response.status_code)


async def test_person_profile_and_joint_trades(client: httpx.AsyncClient) -> None:
    trades = (await client.get("/api/feed", params={"codes": "S", "limit": 200})).json()["items"]
    silver_lake = next(i for i in trades if i["payload"]["ticker"] == "DELL")
    slug = silver_lake["payload"]["owner"]["slug"]
    profile = (await client.get(f"/api/people/{slug}")).json()
    assert profile["entity"]["kind"] == "fund"
    assert profile["stats"]["open_market_sells"] == 16
    assert profile["identifiers"]["sec_cik"] == "0001552054"
    assert profile["summary"]
    rows = (await client.get(f"/api/people/{slug}/trades")).json()["items"]
    assert all(r["code"] in ("P", "S") for r in rows)
    assert all(r["joint_owners"] == 5 for r in rows)
    assert all(r["filing_url"].startswith("https://www.sec.gov/") for r in rows)


async def test_unknown_person_is_404(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/people/nobody-123")).status_code == 404


async def test_trade_detail_has_footnotes_and_owners(client: httpx.AsyncClient) -> None:
    trades = (await client.get("/api/feed", params={"codes": "S", "limit": 200})).json()["items"]
    dell = next(i for i in trades if i["payload"]["ticker"] == "DELL" and i["payload"]["price_is_weighted_avg"])
    detail = (await client.get(f"/api/trades/{dell['transaction_id']}")).json()
    assert len(detail["owners"]) == 5
    assert any("weighted average" in f["text"].lower() for f in detail["footnotes"])
    assert detail["trade"]["code_label"] == "Open-market or private sale"


async def test_ticker_page_events_and_planned_sales(client: httpx.AsyncClient) -> None:
    snow = (await client.get("/api/tickers/SNOW")).json()
    assert snow["security"]["ticker"] == "SNOW"
    events = (await client.get("/api/tickers/SNOW/events")).json()
    layers = {e["layer"] for e in events}
    assert layers == {"insiders", "planned_sales"}
    planned = (await client.get("/api/tickers/SNOW/planned-sales")).json()
    assert planned[0]["status"] == "matched"
    assert (await client.get("/api/tickers/NOPE")).status_code == 404


async def test_search_finds_tickers_and_people(client: httpx.AsyncClient) -> None:
    hits = (await client.get("/api/search", params={"q": "SNOW"})).json()["hits"]
    assert hits[0]["kind"] == "ticker"
    assert hits[0]["label"] == "SNOW"
    people = (await client.get("/api/search", params={"q": "silver lake"})).json()["hits"]
    assert any(h["href"].startswith("/people/") for h in people)


async def test_etag_revalidation_returns_304(client: httpx.AsyncClient) -> None:
    first = await client.get("/api/meta/codes")
    etag = first.headers["etag"]
    second = await client.get("/api/meta/codes", headers={"If-None-Match": etag})
    assert second.status_code == 304
    assert second.content == b""


async def test_status_reports_counts(client: httpx.AsyncClient) -> None:
    status = (await client.get("/api/status")).json()
    assert status["counts"]["filings"] == 4
    assert (await client.get("/api/status/coverage")).status_code == 200


async def test_private_mode_requires_configuration_and_the_passphrase(
        client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    assert (await client.post("/api/private/login", json={"passphrase": "x"})).status_code == 503
    monkeypatch.setenv("PRIVATE_MODE_PASSPHRASE", "correct horse")
    monkeypatch.setenv("SESSION_SECRET", "s" * 40)
    reset_settings_cache()
    try:
        assert (await client.post("/api/private/login", json={"passphrase": "wrong"})).status_code == 401
        ok = await client.post("/api/private/login", json={"passphrase": "correct horse"})
        assert ok.status_code == 200
        assert "httponly" in ok.headers["set-cookie"].lower()
        session = await client.get("/api/private/session")
        assert session.json()["active"] is True
        assert "no-store" in session.headers["cache-control"]
    finally:
        monkeypatch.delenv("PRIVATE_MODE_PASSPHRASE")
        monkeypatch.delenv("SESSION_SECRET")
        reset_settings_cache()


async def test_rate_limit_answers_429() -> None:
    from smt.api.middleware import RateLimitMiddleware

    app = create_app()
    app.user_middleware = [m for m in app.user_middleware if m.cls is not RateLimitMiddleware]
    app.add_middleware(RateLimitMiddleware, rate=0.001, burst=2)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        # Public traffic arrives through Caddy, which adds X-Forwarded-For.
        public = {"X-Forwarded-For": "203.0.113.7"}
        codes = [(await c.get("/api/meta/codes", headers=public)).status_code for _ in range(4)]
        # Internal callers with no visitor behind them (health checks, the worker) are never limited.
        # Page renders are not among them: the web server forwards the visitor's address.
        internal = [(await c.get("/api/meta/codes")).status_code for _ in range(4)]
    assert codes[:2] == [200, 200]
    assert 429 in codes[2:]
    assert internal == [200, 200, 200, 200]


async def test_spot_check_draws_from_every_source(client: httpx.AsyncClient) -> None:
    rows = (await client.get("/api/status/spot-check", params={"limit": 20, "days": 3650})).json()
    sources = {r["source"] for r in rows}
    assert sources == {"form345", "form144"}
    notice = next(r for r in rows if r["source"] == "form144")
    assert notice["transaction_id"] is None
    assert notice["filing_url"].startswith("https://www.sec.gov/")
    assert sum(1 for r in rows if r["source"] == "form345") <= 20


async def test_spot_check_marks_a_value_that_cant_be_right(client: httpx.AsyncClient) -> None:
    """A trade flagged by smt.ingest.quality carries its flag, value and direction, so the Spot
    check reads "Check filing" for it as every table does (web TradeValue)."""
    async with session_scope() as session:
        await session.execute(__import__("sqlalchemy").text(
            "UPDATE transactions SET data_issues = ARRAY['value_implausible'] WHERE code = 'S'"))
    rows = (await client.get("/api/status/spot-check", params={"limit": 100, "days": 3650})).json()
    sales = [r for r in rows if r["source"] == "form345" and r["code"] == "S"]
    assert sales
    assert all(r["data_issues"] == ["value_implausible"] and r["direction"] == "sell" for r in sales)
    assert all(r["value"] is not None for r in sales)
    notice = next(r for r in rows if r["source"] == "form144")
    assert (notice["direction"], notice["data_issues"]) == (None, [])


async def test_spot_check_names_read_as_on_every_other_page(client: httpx.AsyncClient) -> None:
    """Round 6: the spot check printed EDGAR's capitals ('DOCUSIGN, INC.', 'ENTERGY CORP') while
    every other page cases company names (smt.api.queries.entity_link). The company links to its
    ticker page and the filer to their profile, as in the other tables."""
    async with session_scope() as session:
        await session.execute(__import__("sqlalchemy").text(
            "UPDATE entities SET name = 'DELL TECHNOLOGIES INC.' WHERE name = 'Dell Technologies Inc.'"))
    rows = (await client.get("/api/status/spot-check", params={"limit": 100, "days": 3650})).json()
    dell = [r for r in rows if r["source"] == "form345" and r["issuer_ticker"] == "DELL"]
    assert dell
    assert {r["issuer"] for r in dell} == {"Dell Technologies Inc."}
    assert all(r["owner_slug"] for r in rows)
    snowflake = [r for r in rows if r["issuer"] == "Snowflake Inc."]
    assert {r["issuer_ticker"] for r in snowflake} == {"SNOW"}
    assert {r["source"] for r in snowflake} == {"form345", "form144"}


async def test_directory_counts_come_from_the_stats_view(client: httpx.AsyncClient) -> None:
    async with session_scope() as session:
        await session.execute(__import__("sqlalchemy").text("REFRESH MATERIALIZED VIEW entity_trade_stats"))
    page = (await client.get("/api/people/insiders", params={"sort": "sells", "limit": 5})).json()
    top = page["items"][0]
    # The Silver Lake fund in the DELL joint filing: 16 open-market sales.
    assert top["open_market_sells"] == 16
    assert top["role"] is not None

