"""The Live page's today strip, the search palette's labels and the profile summary.

Today strip: one entry per owner in one company, adding up that owner's lots
(Silver Lake's Dell sale is 16 lots on one Form 4, and an owner's filings on one
day add up too), with the total, the lot count and the largest lot, counted by
the same rules as every total (COUNTED, VALUE_OK).

Search: a person is labelled with a role and company ("Director, Snowflake
Inc."), not "Person" again beside the kind column: the role the People directory
shows (LEAD_ROLE_ORDER), so an officer role outranks a more recent board seat.

Summary: an all-caps company name from EDGAR reads in title case ("Fulton
Financial Corp"), and Rule 10b5-1 plan use is given as counts, not "100%".

Real fixtures are loaded through the real loaders. A few tests load a real filing
with some fields changed (another company, a different role, a second sale): the
variants exist only here, to build the cases the real fixtures don't cover.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import text

from smt.api.app import create_app
from smt.api.queries import COUNTED, LEAD_ROLE_ORDER, VALUE_OK
from smt.api.routers.feed import TODAY_TOP, strip_day, today_entries
from smt.api.routers.insiders import _role_rank
from smt.db.session import session_scope
from smt.domain.business_days import ET
from smt.ingest.ownership import FilingMeta, load_ownership
from smt.sources.forms.ownership import PARSER_VERSION, parse_ownership

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]
FIX = Path(__file__).resolve().parents[1] / "fixtures" / "edgar" / "ownership"
DELL = "0001193125-26-396718"  # Silver Lake and four affiliates: 16 open-market sales of DELL
SNOW = "0001821737-26-000013"  # Benoit Dageville, director of Snowflake: 1 sale under a 10b5-1 plan
FULT = "0001592539-26-000001"  # a Form 3: David S. Schulz, director of FULTON FINANCIAL CORP


def _meta(acc: str, form: str, filed: date) -> FilingMeta:
    return FilingMeta(accession=acc, form_type=form, filed_on=filed,
                      accepted_at=datetime(filed.year, filed.month, filed.day, 20, 0, tzinfo=UTC),
                      url=f"https://www.sec.gov/x/{acc}-index.htm",
                      primary_doc_url=f"https://www.sec.gov/x/{acc}.xml", detected_via="feed",
                      parser_version=PARSER_VERSION)


async def _load(filed: date, *accessions: str) -> None:
    """Load real filings as if they were filed on `filed`."""
    async with session_scope() as s:
        for acc in accessions:
            form = "3" if acc == FULT else "4"
            await load_ownership(s, _meta(acc, form, filed), parse_ownership((FIX / f"{acc}.xml").read_bytes()))


#: Changes that turn Dageville's Snowflake Form 4 into the variants below.
OTHER_ISSUER = {"<issuerCik>0001640147</issuerCik>": "<issuerCik>0009999901</issuerCik>",
                "<issuerName>Snowflake Inc.</issuerName>": "<issuerName>EXAMPLE ROBOTICS INC</issuerName>",
                "<issuerTradingSymbol>SNOW</issuerTradingSymbol>": "<issuerTradingSymbol>EXRB</issuerTradingSymbol>"}
SMALLER_SALE = {"<value>50000</value>": "<value>40000</value>"}  # a second, different sale
AS_CEO = {"<isDirector>1</isDirector>": "<isDirector>0</isDirector>",
          "<isOfficer>0</isOfficer>": "<isOfficer>1</isOfficer>",
          "<isOther>0</isOther>": "<isOther>0</isOther><officerTitle>Chief Executive Officer</officerTitle>",
          "<periodOfReport>2026-09-08</periodOfReport>": "<periodOfReport>2026-06-01</periodOfReport>"}
AS_FUND_OWNER = {"<rptOwnerCik>0001821737</rptOwnerCik>": "<rptOwnerCik>0009999902</rptOwnerCik>",
                 "<rptOwnerName>Dageville Benoit</rptOwnerName>":
                     "<rptOwnerName>EXAMPLE CAPITAL PARTNERS LLC</rptOwnerName>",
                 "<isDirector>1</isDirector>": "<isDirector>0</isDirector>",
                 "<isTenPercentOwner>0</isTenPercentOwner>": "<isTenPercentOwner>1</isTenPercentOwner>"}


async def _load_variant(filed: date, accession: str, *changes: dict[str, str]) -> None:
    """Load Dageville's Snowflake Form 4 with `changes` applied, under a new accession."""
    xml = (FIX / f"{SNOW}.xml").read_text(encoding="utf-8")
    for change in changes:
        for old, new in change.items():
            assert xml.count(old) == 1, old
            xml = xml.replace(old, new)
    async with session_scope() as s:
        await load_ownership(s, _meta(accession, "4", filed), parse_ownership(xml.encode()))


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """The three filings, filed on the day the today strip covers (today, US Eastern, or the
    latest SEC business day on a weekend or holiday), served in-process."""
    await _load(strip_day(datetime.now(ET).date()), DELL, SNOW, FULT)
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _counted_lots(day: date, direction: str) -> dict[tuple[int, int], list[tuple[int, Decimal]]]:
    """The lots the strip may use, straight from the shared counting rules, by (owner, company)."""
    async with session_scope() as s:
        rows = (await s.execute(text(
            "SELECT t.id, t.entity_id, t.issuer_entity_id, t.value FROM transactions t "
            "JOIN filings f ON f.id = t.filing_id "
            f"WHERE f.filed_on = :day AND t.direction = :dir AND {COUNTED} AND {VALUE_OK} "
            "AND t.value IS NOT NULL"), {"day": day, "dir": direction})).all()
    lots: dict[tuple[int, int], list[tuple[int, Decimal]]] = defaultdict(list)
    for r in rows:
        lots[(r.entity_id, r.issuer_entity_id)].append((r.id, r.value))
    return lots


async def test_one_entry_per_owner_and_company_adding_up_the_lots() -> None:
    day = date(2026, 9, 21)
    await _load(day, DELL, SNOW)
    expected = await _counted_lots(day, "sell")
    async with session_scope() as s:
        entries = await today_entries(s, day, "sell")
    # Silver Lake's 16 lots are one entry, Dageville's single sale another.
    assert len(entries) == len(expected) == 2
    assert len({(e.owner.slug, e.ticker) for e in entries}) == len(entries)
    assert [e.value for e in entries] == sorted((e.value for e in entries), reverse=True)
    for e in entries:
        async with session_scope() as s:
            key = (await s.execute(text("SELECT entity_id, issuer_entity_id FROM transactions WHERE id = :i"),
                                   {"i": e.largest_transaction_id})).one()
        lots = expected[(key.entity_id, key.issuer_entity_id)]
        assert e.lots == len(lots)
        assert e.value == sum(v for _, v in lots)
        assert e.lot_values == sorted((v for _, v in lots), reverse=True)
        assert e.largest_transaction_id == min(lots, key=lambda lot: (-lot[1], lot[0]))[0]
    dell = next(e for e in entries if e.ticker == "DELL")
    assert dell.lots == 16
    assert dell.shares is not None
    assert dell.shares > 0
    assert dell.first_traded_on is not None
    assert dell.last_traded_on is not None
    assert dell.first_traded_on <= dell.last_traded_on
    snow = next(e for e in entries if e.ticker == "SNOW")
    assert (snow.owner.name, snow.lots, snow.role) == ("Benoit Dageville", 1, "Director")
    assert snow.issuer == "Snowflake Inc."


async def test_lots_left_out_of_totals_are_left_out_of_the_strip() -> None:
    day = date(2026, 9, 21)
    await _load(day, DELL)
    async with session_scope() as s:
        before = (await today_entries(s, day, "sell"))[0]
        # Mark the largest lot as a second copy of another row (as mark_duplicates would):
        # COUNTED leaves it out, so the entry loses that lot.
        await s.execute(text("UPDATE transactions SET duplicate_of_id = (SELECT min(id) FROM transactions "
                             "WHERE id <> :i) WHERE id = :i"), {"i": before.largest_transaction_id})
    async with session_scope() as s:
        after = (await today_entries(s, day, "sell"))[0]
    assert after.lots == before.lots - 1
    assert after.value == before.value - before.lot_values[0]
    assert after.largest_transaction_id != before.largest_transaction_id


async def test_one_owners_filings_on_one_day_are_one_entry() -> None:
    day = date(2026, 9, 21)
    await _load(day, SNOW)
    await _load_variant(day, "0001821737-26-900001", SMALLER_SALE)
    async with session_scope() as s:
        entries = await today_entries(s, day, "sell")
    assert len(entries) == 1
    (e,) = entries
    assert (e.owner.name, e.ticker, e.lots) == ("Benoit Dageville", "SNOW", 2)
    assert e.value == sum(e.lot_values)
    assert e.lot_values[0] > e.lot_values[1]  # the real 50,000-share sale is the larger lot


async def test_one_owner_in_two_companies_is_two_entries() -> None:
    day = date(2026, 9, 21)
    await _load(day, SNOW)
    await _load_variant(day, "0001821737-26-900002", OTHER_ISSUER)
    async with session_scope() as s:
        entries = await today_entries(s, day, "sell")
    assert sorted((e.owner.name, e.ticker, e.lots) for e in entries) == [
        ("Benoit Dageville", "EXRB", 1), ("Benoit Dageville", "SNOW", 1)]
    assert {e.issuer for e in entries} == {"Snowflake Inc.", "Example Robotics Inc"}


async def test_a_fund_owner_reads_like_a_company(client: httpx.AsyncClient) -> None:
    day = datetime.now(ET).date()
    # (A smaller sale, or it would be the same trade as Dageville's indirect one, filed the same day.)
    await _load_variant(day, "0009999902-26-000001", AS_FUND_OWNER, SMALLER_SALE)
    async with session_scope() as s:
        entry = next(e for e in await today_entries(s, day, "sell") if e.owner.slug.startswith("example"))
        slug = entry.owner.slug
    # EDGAR's capitals are title-cased beside the issuer, in the strip and in the summary.
    assert (entry.owner.name, entry.role) == ("Example Capital Partners LLC", "10% owner")
    summary = (await client.get(f"/api/people/{slug}")).json()["summary"]
    assert summary.startswith("Example Capital Partners LLC is a 10% owner of Snowflake Inc. ")


async def test_the_strip_shows_at_most_its_limit() -> None:
    day = date(2026, 9, 21)
    await _load(day, DELL, SNOW)
    async with session_scope() as s:
        top = await today_entries(s, day, "sell", limit=1)
        assert await today_entries(s, day, "buy") == []
    assert len(top) == 1
    assert top[0].ticker == "DELL"  # the larger total


async def test_today_endpoint_groups_lots(client: httpx.AsyncClient) -> None:
    body = (await client.get("/api/feed/today")).json()
    today = datetime.now(ET).date()
    assert body["day"] == strip_day(today).isoformat()
    assert body["edgar_open_today"] is (strip_day(today) == today)
    sells = body["largest_sells"]
    assert 0 < len(sells) <= TODAY_TOP
    assert len({(e["owner"]["slug"], e["ticker"]) for e in sells}) == len(sells)
    dell = next(e for e in sells if e["ticker"] == "DELL")
    assert dell["lots"] == len(dell["lot_values"]) == 16
    assert Decimal(dell["value"]) == sum(Decimal(v) for v in dell["lot_values"])
    trade = (await client.get(f"/api/trades/{dell['largest_transaction_id']}")).json()
    assert Decimal(trade["trade"]["value"]) == Decimal(dell["lot_values"][0])


async def test_search_labels_a_person_with_a_role_and_company(client: httpx.AsyncClient) -> None:
    hits = (await client.get("/api/search", params={"q": "dageville"})).json()["hits"]
    person = next(h for h in hits if h["label"] == "Benoit Dageville")
    assert person["kind"] == "person"
    assert person["sublabel"] == "Director, Snowflake Inc."
    hits = (await client.get("/api/search", params={"q": "schulz"})).json()["hits"]
    person = next(h for h in hits if h["href"].startswith("/people/"))
    assert person["sublabel"] == "Director, Fulton Financial Corp"  # EDGAR's capitals title-cased


async def test_search_and_directory_introduce_a_person_by_the_same_role() -> None:
    # Like Tim Cook (Apple's CEO, and a NIKE director on his latest filing): Dageville as the
    # CEO of another company on an older filing, and a Snowflake director on a newer one.
    day = datetime.now(ET).date()
    await _load(day, SNOW)
    await _load_variant(day, "0001821737-26-900003", OTHER_ISSUER, AS_CEO)
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        hits = (await c.get("/api/search", params={"q": "dageville"})).json()["hits"]
        rows = (await c.get("/api/people/insiders", params={"q": "dageville"})).json()["items"]
        person = next(h for h in hits if h["label"] == "Benoit Dageville")
        (row,) = rows
        summary = (await c.get(f"/api/people/{row['entity']['slug']}")).json()["summary"]
    assert person["sublabel"] == f"{row['role']}, {row['organization']['name']}"
    assert person["sublabel"] == "Chief Executive Officer, Example Robotics Inc"
    assert summary.startswith("Benoit Dageville is the Chief Executive Officer of Example Robotics Inc")


async def test_the_sql_role_order_matches_the_summary_order() -> None:
    """LEAD_ROLE_ORDER (directory, search) and _role_rank (profile summary) sort alike."""
    roles = [  # (id, role_type, title, last_seen_on)
        (1, "director", "", date(2026, 9, 1)),
        (2, "officer", "Chief Executive Officer", date(2026, 4, 2)),
        (3, "officer", "EVP, CFO", date(2026, 9, 3)),
        (4, "officer", "Vice President", date(2026, 9, 5)),
        (5, "officer", "PRESIDENT & CEO", date(2026, 1, 1)),
        (6, "ten_pct_owner", "", date(2026, 9, 9)),
        (7, "director", "", date(2026, 9, 1)),  # ties with 1 on the date: the newer row first
        (8, "other_insider", "Member of 13(d) group", None),
        (9, "director", "", None),
        (10, "officer", "Chairman", date(2026, 8, 1)),
        (11, "officer", "", date(2026, 9, 20)),
        (12, "officer", "Chief Operating Officer", None),
    ]
    expected = [r.id for r in sorted((SimpleNamespace(id=i, role_type=k, title=t, last_seen_on=d)
                                      for i, k, t, d in roles), key=_role_rank)]
    async with session_scope() as s:
        got = (await s.execute(text(
            "SELECT r.id FROM unnest(CAST(:ids AS bigint[]), CAST(:kinds AS text[]), CAST(:titles AS text[]), "
            f"CAST(:seen AS date[])) AS r(id, role_type, title, last_seen_on) ORDER BY {LEAD_ROLE_ORDER}"),
            {"ids": [r[0] for r in roles], "kinds": [r[1] for r in roles], "titles": [r[2] for r in roles],
             "seen": [r[3] for r in roles]})).scalars().all()
    assert list(got) == expected
    assert expected[:3] == [2, 5, 3]  # CEO, then the older "President & CEO" (same rank), then the CFO


async def test_summaries_read_naturally(client: httpx.AsyncClient) -> None:
    hits = (await client.get("/api/search", params={"q": "dageville"})).json()["hits"]
    slug = next(h for h in hits if h["label"] == "Benoit Dageville")["href"].removeprefix("/people/")
    summary = (await client.get(f"/api/people/{slug}")).json()["summary"]
    assert summary.startswith("Benoit Dageville is a Director of Snowflake Inc. ")
    assert "The open-market sale was made under a Rule 10b5-1 plan." in summary
    assert "%" not in summary
    hits = (await client.get("/api/search", params={"q": "schulz"})).json()["hits"]
    slug = next(h for h in hits if h["href"].startswith("/people/"))["href"].removeprefix("/people/")
    summary = (await client.get(f"/api/people/{slug}")).json()["summary"]
    assert "is a Director of Fulton Financial Corp" in summary
    assert "FULTON" not in summary


async def test_a_whole_word_match_outranks_a_near_one() -> None:
    """'feiger' must put Mitchell Stuart Feiger before Peter Feigin."""
    from smt.api.app import create_app

    async with session_scope() as session:
        await session.execute(text(
            "INSERT INTO entities (kind, name, sort_name, slug) VALUES "
            "('person', 'Peter Feigin', 'Feigin, Peter', 'peter-feigin-1'), "
            "('person', 'Mitchell Stuart Feiger', 'Feiger, Mitchell Stuart', 'mitchell-stuart-feiger-2')"))
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        hits = (await client.get("/api/search", params={"q": "feiger"})).json()["hits"]
    people = [h["label"] for h in hits if h["kind"] == "person"]
    assert people[0] == "Mitchell Stuart Feiger"


async def test_search_finds_a_company_that_files_as_an_insider_and_has_no_ticker() -> None:
    """Round 6: organisations that had been stored as people (MetLife, Thrivent, 'Sport City
    Cadiz S.L.') become companies. A company is found under its ticker, so one with no ticker
    that files as an insider or as a Form 144 seller is found under /people, its name as its
    profile shows it. A company with a ticker stays under /tickers only, and a ticker-less
    issuer that never filed either way has no /people page to find.

    Golden case: Metropolitan Life Insurance Co (entity 62148) files only Form 144s and is
    stored with the tags {form144_filer,name_from_sec_record} and no 'insider' tag, so the
    fixture uses exactly those tags (pre-review 7: search missed it)."""
    from smt.api.app import create_app

    async with session_scope() as session:
        await session.execute(text(
            "INSERT INTO entities (kind, name, sort_name, slug, tags) VALUES "
            "('company', 'METROPOLITAN LIFE INSURANCE CO', 'METROPOLITAN LIFE INSURANCE CO', "
            " 'metropolitan-life-insurance-co-728618', ARRAY['form144_filer', 'name_from_sec_record']), "
            "('company', 'Metropolitan Bank Holding Corp', 'Metropolitan Bank Holding Corp', "
            " 'metropolitan-bank-holding-corp-1', ARRAY['insider']), "
            "('company', 'Metropolitan Edison Co', 'Metropolitan Edison Co', 'metropolitan-edison-co-2', "
            " ARRAY['issuer'])"))
        await session.execute(text(
            "INSERT INTO securities (issuer_entity_id, name, primary_ticker, sector_source) "
            "SELECT id, name, 'MCB', 'unmapped' FROM entities WHERE slug = 'metropolitan-bank-holding-corp-1'"))
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        hits = (await client.get("/api/search", params={"q": "metropolitan"})).json()["hits"]
    found = {(h["label"], h["kind"], h["href"]) for h in hits}
    assert ("Metropolitan Life Insurance Co", "company", "/people/metropolitan-life-insurance-co-728618") in found
    assert ("Metropolitan Bank Holding Corp", "company", "/tickers/MCB") in found
    assert not any(h["href"] == "/people/metropolitan-bank-holding-corp-1" for h in hits)
    assert not any("Edison" in h["label"] for h in hits)


async def test_search_writes_a_fund_name_as_its_profile_does() -> None:
    """A fund, bank or index manager is stored as EDGAR spells it ('CANTOR FITZGERALD, L. P.',
    'GENERAL ATLANTIC, L.P.'); search labels it as the People directory and its profile do
    (entity_link), not in capitals. A person's name is shown as stored."""
    from smt.api.app import create_app

    async with session_scope() as session:
        await session.execute(text(
            "INSERT INTO entities (kind, name, sort_name, slug, tags) VALUES "
            "('fund', 'CANTOR FITZGERALD, L. P.', 'CANTOR FITZGERALD, L. P.', 'cantor-fitzgerald-l-p-1', "
            " ARRAY['insider', 'ten_pct_owner']), "
            "('bank', 'CANTOR BANK N.A.', 'CANTOR BANK N.A.', 'cantor-bank-n-a-2', ARRAY['insider']), "
            "('person', 'Cantor Jane', 'Cantor, Jane', 'cantor-jane-3', ARRAY['insider'])"))
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        hits = (await client.get("/api/search", params={"q": "cantor"})).json()["hits"]
    labels = {h["href"]: h["label"] for h in hits}
    assert labels["/people/cantor-fitzgerald-l-p-1"] == "Cantor Fitzgerald, L. P."
    assert labels["/people/cantor-bank-n-a-2"] == "Cantor Bank N.A."
    assert labels["/people/cantor-jane-3"] == "Cantor Jane"


async def test_live_rows_word_the_role_like_the_profile(client: httpx.AsyncClient) -> None:
    """The feed payload keeps the title as typed ("Director, CO-FOUNDER"); the Live rows read it
    in the site's wording, from the filing's relationship (smt.api.wording.relationship_role)."""
    as_cofounder = {"<isOther>0</isOther>": "<isOther>1</isOther><otherText>CO-FOUNDER</otherText>"}
    await _load_variant(date(2026, 9, 22), "0001821737-26-900004", SMALLER_SALE, as_cofounder)
    async with session_scope() as s:
        stored = set((await s.execute(text(
            "SELECT payload->'owner'->>'role' FROM feed_events WHERE payload->>'accession' = :acc"),
            {"acc": "0001821737-26-900004"})).scalars())
    assert stored == {"Director, CO-FOUNDER"}  # as the filer typed it
    items = (await client.get("/api/feed", params={"codes": "all", "limit": 200})).json()["items"]
    item = next(i for i in items if i["payload"]["accession"] == "0001821737-26-900004")
    assert item["payload"]["owner"]["role"] == "Director, Co-Founder"
    assert item["payload"]["owner"]["name"] == "Benoit Dageville"  # the current name is still applied
    # A plain director's row is unchanged.
    plain = next(i for i in items if i["payload"]["accession"] == SNOW)
    assert plain["payload"]["owner"]["role"] == "Director"


async def test_filings_on_record_count_a_form_3(client: httpx.AsyncClient) -> None:
    """A Form 3 (holdings on becoming an insider, no trades) is a filing on record: the profile's
    "Filings on record here" range includes it."""
    hits = (await client.get("/api/search", params={"q": "schulz"})).json()["hits"]
    slug = next(h for h in hits if h["href"].startswith("/people/"))["href"].removeprefix("/people/")
    stats = (await client.get(f"/api/people/{slug}")).json()["stats"]
    filed = strip_day(datetime.now(ET).date()).isoformat()
    assert stats["trades"] == 0
    assert (stats["first_filed"], stats["last_filed"]) == (filed, filed)  # was none: no trades
