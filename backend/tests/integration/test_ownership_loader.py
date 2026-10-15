"""Loading real filings into the database: idempotency, attribution, amendments, flags, matching.

Every fixture is a real EDGAR document (tests/fixtures/edgar). Expectations
about the documents were read from the XML by hand; expectations about the
loader follow the rules in smt.ingest.ownership.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from smt.db.models import (
    FeedEvent,
    Filing,
    InsiderHolding,
    PlannedSale,
    PlannedSaleMatch,
    Quarantine,
    Role,
    Transaction,
    TransactionOwner,
)
from smt.db.session import session_scope
from smt.ingest.form144 import load_form144, match_sales_for
from smt.ingest.ownership import FilingMeta, LoadResult, load_ownership
from smt.sources.forms.form144 import parse_form144
from smt.sources.forms.ownership import PARSER_VERSION, parse_ownership

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]
FIX = Path(__file__).resolve().parents[1] / "fixtures" / "edgar"


def meta(accession: str, form: str, filed_on: date, accepted_at: datetime | None = None,
         live: bool = True) -> FilingMeta:
    return FilingMeta(
        accession=accession, form_type=form, filed_on=filed_on, accepted_at=accepted_at,
        url=f"https://www.sec.gov/Archives/edgar/data/0/{accession.replace('-', '')}/{accession}-index.htm",
        primary_doc_url=None, detected_via="manual", parser_version=PARSER_VERSION, live=live,
    )


async def load(accession: str, form: str, filed_on: date, accepted_at: datetime | None = None) -> LoadResult:
    doc = parse_ownership((FIX / "ownership" / f"{accession}.xml").read_bytes())
    async with session_scope() as session:
        return await load_ownership(session, meta(accession, form, filed_on, accepted_at), doc)


async def count(model: type) -> int:
    async with session_scope() as session:
        return int((await session.execute(select(func.count()).select_from(model))).scalar_one())


DELL = "0001193125-26-396718"   # Silver Lake entities selling Dell Class C, 5 joint owners
DELL_ACCEPTED = datetime(2026, 9, 21, 20, 30, 41, tzinfo=UTC)  # 16:30:41 EDT


async def test_dell_joint_filing_loads_once_per_row() -> None:
    result = await load(DELL, "4", date(2026, 9, 21), DELL_ACCEPTED)
    # 17 non-derivative + 1 derivative transaction rows; 6 holding rows.
    assert result.new_rows == 18
    assert await count(Transaction) == 18
    assert await count(InsiderHolding) == 6
    # Every transaction lists all five owners, but counts once.
    assert await count(TransactionOwner) == 18 * 5
    assert len(result.feed_event_ids) == 18


async def test_loading_twice_changes_nothing() -> None:
    await load(DELL, "4", date(2026, 9, 21), DELL_ACCEPTED)
    before = {m.__name__: await count(m) for m in (Filing, Transaction, TransactionOwner, InsiderHolding,
                                                   FeedEvent, Role)}
    second = await load(DELL, "4", date(2026, 9, 21), DELL_ACCEPTED)
    after = {m.__name__: await count(m) for m in (Filing, Transaction, TransactionOwner, InsiderHolding,
                                                  FeedEvent, Role)}
    assert second.new_rows == 0
    assert second.feed_event_ids == []   # never re-announced on the Live feed
    assert before == after


async def test_joint_filing_attributed_to_the_fund() -> None:
    await load(DELL, "4", date(2026, 9, 21), DELL_ACCEPTED)
    async with session_scope() as session:
        row = (await session.execute(text(
            "SELECT e.name, e.kind FROM transactions t JOIN entities e ON e.id = t.entity_id LIMIT 1"))).one()
    assert row.kind == "fund"
    assert row.name.startswith("Silver Lake Partners")


async def test_exercise_and_sell_grouped_and_only_p_s_are_market_trades() -> None:
    await load(DELL, "4", date(2026, 9, 21), DELL_ACCEPTED)
    async with session_scope() as session:
        rows = (await session.execute(select(
            Transaction.code, Transaction.direction, Transaction.is_open_market, Transaction.exercise_and_sell_group,
            Transaction.value, Transaction.shares, Transaction.price, Transaction.line_no, Transaction.table_name,
        ).order_by(Transaction.table_name.desc(), Transaction.line_no))).all()
    conversion = rows[0]
    assert conversion.code == "M"
    assert conversion.direction == "other"
    assert not conversion.is_open_market
    assert conversion.exercise_and_sell_group == f"{DELL}:2026-09-17"
    first_sale = rows[1]
    assert (first_sale.code, first_sale.direction, first_sale.is_open_market) == ("S", "sell", True)
    # 406 shares at $577.60 (weighted average) = $234,505.60
    assert first_sale.value == Decimal("234505.60")
    assert all(r.exercise_and_sell_group == f"{DELL}:2026-09-17" for r in rows if r.table_name == "nonderivative")


async def test_form4_filed_on_deadline_is_not_late() -> None:
    # Trade Thu 2026-09-17 -> due by end of Mon 2026-09-21 (two business days) -> filed 9/21.
    await load(DELL, "4", date(2026, 9, 21), DELL_ACCEPTED)
    async with session_scope() as session:
        flags = (await session.execute(select(Transaction.deadline_on, Transaction.is_late).distinct())).all()
    assert flags == [(date(2026, 9, 21), False)]


async def test_form4_filed_after_deadline_is_late_with_business_days() -> None:
    await load(DELL, "4", date(2026, 9, 24), datetime(2026, 9, 24, 14, 0, tzinfo=UTC))
    async with session_scope() as session:
        row = (await session.execute(select(Transaction.is_late, Transaction.business_days_late,
                                            Transaction.delay_days).limit(1))).one()
    assert row.is_late is True
    assert row.business_days_late == 3   # 9/22, 9/23, 9/24
    assert row.delay_days == 7           # 9/17 -> 9/24


GHM_ORIGINAL = "0000891839-25-000050"   # P 1,200 shares @ $49.01 reported as DIRECT
GHM_AMENDMENT = "0000891839-26-000409"  # 4/A: same purchase, corrected to INDIRECT (family trust)


async def _ghm_rows() -> list[tuple[str, str | None, bool, str | None]]:
    async with session_scope() as session:
        return [tuple(r) for r in (await session.execute(text(
            "SELECT f.source_key, t.ownership_nature, t.is_current, t.review_note FROM transactions t "
            "JOIN filings f ON f.id = t.filing_id ORDER BY f.filed_on"))).all()]


async def test_amendment_supersedes_the_corrected_row() -> None:
    await load(GHM_ORIGINAL, "4", date(2025, 9, 15))
    await load(GHM_AMENDMENT, "4/A", date(2026, 9, 21))
    assert await _ghm_rows() == [(GHM_ORIGINAL, "D", False, None), (GHM_AMENDMENT, "I", True, None)]
    async with session_scope() as session:
        amendment = (await session.execute(select(Filing).where(Filing.source_key == GHM_AMENDMENT))).scalar_one()
        original = (await session.execute(select(Filing).where(Filing.source_key == GHM_ORIGINAL))).scalar_one()
    assert amendment.amends_filing_id == original.id


async def test_reloading_an_amendment_changes_nothing() -> None:
    """A 4/A loaded twice (a retry, a reprocess) keeps its pairing and sets nothing aside again."""
    await load(GHM_ORIGINAL, "4", date(2025, 9, 15))
    await load(GHM_AMENDMENT, "4/A", date(2026, 9, 21))
    await load(GHM_AMENDMENT, "4/A", date(2026, 9, 21))
    assert await _ghm_rows() == [(GHM_ORIGINAL, "D", False, None), (GHM_AMENDMENT, "I", True, None)]
    assert await count(Quarantine) == 0
    async with session_scope() as session:
        flags = (await session.execute(text("SELECT count(*) FROM transactions WHERE cardinality(data_issues) > 0"))).scalar_one()
    assert flags == 0


async def test_amendment_arriving_before_its_original_is_linked_later() -> None:
    await load(GHM_AMENDMENT, "4/A", date(2026, 9, 21))
    assert [r[2] for r in await _ghm_rows()] == [True]   # nothing to supersede yet
    await load(GHM_ORIGINAL, "4", date(2025, 9, 15))
    assert await _ghm_rows() == [(GHM_ORIGINAL, "D", False, None), (GHM_AMENDMENT, "I", True, None)]


async def test_amendment_is_judged_by_the_original_filing_date() -> None:
    # Trade Fri 2025-09-12 -> due Tue 2025-09-16. Original filed 9/15: on time, even though the
    # 4/A came a year later.
    await load(GHM_AMENDMENT, "4/A", date(2026, 9, 21))
    async with session_scope() as session:
        late = (await session.execute(select(Transaction.is_late))).scalar_one()
    assert late is False


async def test_poa_only_amendment_without_original_supersedes_nothing() -> None:
    result = await load("0000320187-26-000129", "4/A", date(2026, 9, 21))
    assert "original filing not in database yet" in result.notes
    assert await count(Quarantine) == 0
    async with session_scope() as session:
        current = (await session.execute(select(Transaction.is_current))).scalars().all()
    assert current == [True]


async def test_form3_records_insider_since() -> None:
    doc = parse_ownership((FIX / "ownership" / "0001592539-26-000001.xml").read_bytes())
    await load("0001592539-26-000001", "3", date(2026, 9, 21))
    async with session_scope() as session:
        roles = (await session.execute(select(Role.role_type, Role.start_date))).all()
        events = (await session.execute(select(FeedEvent.event_type))).scalars().all()
    assert roles
    assert all(start == doc.period_of_report for _type, start in roles)
    assert events == ["insider_holding"]
    assert await count(Transaction) == 0
    assert await count(InsiderHolding) == 1


async def test_form4_transaction_reported_on_form5_is_late() -> None:
    # THO Form 5: an F-code row with transactionFormType 4, i.e. a Form 4 transaction filed late.
    await load("0001488301-26-000002", "5", date(2026, 9, 2))
    async with session_scope() as session:
        row = (await session.execute(select(Transaction.is_late, Transaction.deadline_rule))).one()
    assert row.is_late is True
    assert row.deadline_rule == "reported_on_form5"


async def test_backfill_loads_never_reach_the_live_feed() -> None:
    doc = parse_ownership((FIX / "ownership" / f"{DELL}.xml").read_bytes())
    async with session_scope() as session:
        await load_ownership(session, meta(DELL, "4", date(2026, 9, 21), DELL_ACCEPTED, live=False), doc)
        live = (await session.execute(select(func.count()).where(FeedEvent.is_live.is_(True)))).scalar_one()
    assert live == 0


async def test_form144_is_matched_to_the_form4_that_executed_it() -> None:
    # SNOW: Benoit Dageville planned to sell 50,000 shares (144 filed 2026-09-09); his Form 4
    # reports an S of 50,000 shares on 2026-09-09.
    notice = parse_form144((FIX / "form144" / "0001959173-26-006819.xml").read_bytes())
    async with session_scope() as session:
        await load_form144(session, meta("0001959173-26-006819", "144", date(2026, 9, 9)), notice)
    await load("0001821737-26-000013", "4", date(2026, 9, 10))
    async with session_scope() as session:
        sale = (await session.execute(select(PlannedSale))).scalar_one()
        seller_ids = [sale.seller_entity_id]
        assert sale.issuer_entity_id is not None
        await match_sales_for(session, seller_ids, sale.issuer_entity_id)
    async with session_scope() as session:
        sale = (await session.execute(select(PlannedSale))).scalar_one()
        matches = (await session.execute(select(func.count()).select_from(PlannedSaleMatch))).scalar_one()
    assert sale.status == "matched"
    assert sale.shares == Decimal(50000)
    assert sale.matched_shares == Decimal(50000)
    assert matches == 1


async def test_one_form4_sale_is_linked_to_one_notice_only() -> None:
    # SNOW: two notices by the same seller (0001959173-26-006819 filed 09-09, and 0001959173-26-007084
    # filed 09-10, which repeats the past sales) and one Form 4 sale of 50,000 shares on 09-09.
    for accession, filed in (("0001959173-26-006819", date(2026, 9, 9)), ("0001959173-26-007084", date(2026, 9, 10))):
        notice = parse_form144((FIX / "form144" / f"{accession}.xml").read_bytes())
        async with session_scope() as session:
            await load_form144(session, meta(accession, "144", filed), notice)
    await load("0001821737-26-000013", "4", date(2026, 9, 10))
    async with session_scope() as session:
        sales = (await session.execute(select(PlannedSale).order_by(PlannedSale.id))).scalars().all()
        await match_sales_for(session, [sales[0].seller_entity_id], sales[0].issuer_entity_id or 0)
        await match_sales_for(session, [sales[0].seller_entity_id], sales[0].issuer_entity_id or 0)  # re-run
    async with session_scope() as session:
        links = (await session.execute(select(PlannedSaleMatch.transaction_id, PlannedSaleMatch.planned_sale_id)
                                       )).all()
        sales = (await session.execute(select(PlannedSale).order_by(PlannedSale.id))).scalars().all()
    assert len(links) == 1  # the sale counts toward one notice, not both
    assert sum(1 for s in sales if s.status == "matched") == 1
    assert all(s.matched_shares is None or s.matched_shares <= (s.shares or 0) for s in sales)


async def test_a_ticker_belonging_to_another_company_is_not_linked() -> None:
    """VineBrook and 6 others wrote 'NA' (meaning none), which is Nano Labs' real ticker."""
    import dataclasses

    async with session_scope() as session:
        await session.execute(text(
            "INSERT INTO entities (kind, name, sort_name, slug) VALUES ('company', 'Nano Labs Ltd', 'Nano Labs Ltd', "
            "'nano-labs-1'); INSERT INTO securities (issuer_entity_id, name, primary_ticker, sector_source) "
            "SELECT id, 'Nano Labs Ltd', 'NA', 'unmapped' FROM entities WHERE slug = 'nano-labs-1'; "
            "INSERT INTO security_ids (security_id, scheme, value, source) "
            "SELECT id, 'ticker', 'NA', 'test' FROM securities WHERE primary_ticker = 'NA'"))
    doc = parse_ownership((FIX / "ownership" / f"{DELL}.xml").read_bytes())
    async with session_scope() as session:
        await load_ownership(session, meta(DELL, "4", date(2026, 9, 21)),
                             dataclasses.replace(doc, issuer_ticker="NA"))
    async with session_scope() as session:
        linked = (await session.execute(text(
            "SELECT count(*) FROM transactions t JOIN securities s ON s.id = t.security_id "
            "WHERE s.primary_ticker = 'NA'"))).scalar_one()
        payload_tickers = set((await session.execute(text(
            "SELECT payload->>'ticker' FROM feed_events WHERE event_type = 'insider_trade'"))).scalars())
        new_na = (await session.execute(text("SELECT count(*) FROM securities WHERE primary_ticker = 'NA'"))).scalar_one()
    assert linked == 0
    assert payload_tickers == {None}
    assert new_na == 1  # no second "NA" security was created for Dell


async def test_placeholder_securities_are_repaired_and_not_created_again() -> None:
    """FS Credit REIT wrote '[NONE', which once became a ticker page of its own."""
    import dataclasses

    from smt.ingest.entities import repair_security_links

    doc = parse_ownership((FIX / "ownership" / f"{DELL}.xml").read_bytes())
    async with session_scope() as session:
        await load_ownership(session, meta(DELL, "4", date(2026, 9, 21)), dataclasses.replace(doc, issuer_ticker="[NONE"))
        assert (await session.execute(text("SELECT count(*) FROM securities"))).scalar_one() == 0
        # One made before the check existed: rows and feed events point at a "[NONE" security.
        await session.execute(text(
            "INSERT INTO securities (issuer_entity_id, name, primary_ticker, sector_source) "
            "SELECT issuer_entity_id, 'Dell', '[NONE', 'unmapped' FROM transactions LIMIT 1; "
            "INSERT INTO security_ids (security_id, scheme, value, source) "
            "SELECT id, 'ticker', '[NONE', 'edgar_filing' FROM securities; "
            "UPDATE transactions SET security_id = (SELECT id FROM securities); "
            "UPDATE feed_events SET security_id = (SELECT id FROM securities), "
            "payload = jsonb_set(payload, '{ticker}', '\"[NONE\"'::jsonb)"))
        moved = await repair_security_links(session)
        left = (await session.execute(text("SELECT count(*) FROM securities"))).scalar_one()
        linked = (await session.execute(text(
            "SELECT count(*) FROM transactions WHERE security_id IS NOT NULL"))).scalar_one()
        tickers = set((await session.execute(text("SELECT payload->>'ticker' FROM feed_events"))).scalars())
        raw = set((await session.execute(text("SELECT raw_ticker FROM transactions"))).scalars())
    assert moved > 0
    assert left == 0
    assert linked == 0
    assert tickers == {None}
    assert raw == {"[NONE"}  # the filing's own text is kept on the row


async def test_a_row_an_amendment_adds_counts_in_totals() -> None:
    """A 4/A that adds a trade the original left out (no original row it could repeat) counts."""
    import dataclasses
    from decimal import Decimal as _D

    await load(GHM_ORIGINAL, "4", date(2025, 9, 15))
    doc = parse_ownership((FIX / "ownership" / f"{GHM_AMENDMENT}.xml").read_bytes())
    restated = doc.rows[0]
    added = dataclasses.replace(restated, line_no=restated.line_no + 1, code="S", shares=_D("300"),
                                acquired_disposed="D")
    amended = dataclasses.replace(doc, rows=(*doc.rows, added))
    async with session_scope() as session:
        await load_ownership(session, meta(GHM_AMENDMENT, "4/A", date(2026, 9, 21)), amended)
    async with session_scope() as session:
        rows = (await session.execute(text(
            "SELECT t.code, t.is_current, t.data_issues, t.review_note FROM transactions t "
            "JOIN filings f ON f.id = t.filing_id WHERE f.source_key = :a ORDER BY t.line_no"),
            {"a": GHM_AMENDMENT})).all()
    by_code = {r.code: r for r in rows}
    assert by_code["S"].data_issues == []           # counted
    assert "added by the amendment" in (by_code["S"].review_note or "")
    assert by_code["P"].data_issues == []           # paired with the original's P
    # A review item left from before this rule is closed by the next re-check.
    from smt.ingest.ownership import repair_amendments

    line = (await _scalar("SELECT t.table_name || ':' || t.line_no FROM transactions t JOIN filings f "
                          "ON f.id = t.filing_id WHERE f.source_key = :a AND t.code = 'S'", a=GHM_AMENDMENT))
    async with session_scope() as session:
        await session.execute(text(
            "INSERT INTO quarantine (source, filing_ref, row_ref, reason, payload) "
            "VALUES ('edgar', :a, :r, '4/A row could not be paired with its original', '{}')"),
            {"a": GHM_AMENDMENT, "r": line})
        await repair_amendments(session)
    assert await _scalar("SELECT resolution FROM quarantine WHERE row_ref = :r", r=line) ==         "counted as a trade the original left out"


async def _scalar(sql: str, **params: object) -> object:
    async with session_scope() as session:
        return (await session.execute(text(sql), params)).scalar_one()
