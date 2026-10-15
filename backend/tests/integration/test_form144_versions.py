"""Form 144 amendments: only the current version of a notice holds Form 4 links.

The notice and the Form 4 are real EDGAR documents (tests/fixtures/edgar):
* 0001959173-26-006819: Benoit Dageville (Snowflake) plans to sell 50,000
  shares, filed 2026-09-09;
* 0001821737-26-000013: his Form 4, an S of 50,000 shares on 2026-09-09.

EDGAR has no 144/A for this notice. The amendments below are test-only copies
of the real notice with the two amendment fields set (submission type 144/A
and the accession it amends); their accessions start with TEST so they can't
be mistaken for real filings. They are filed days after the original and after
the sale, as real 144/As usually are (planned sale 352's 144/A was filed three
days after both).
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import NamedTuple

import httpx
import pytest
from sqlalchemy import select, text

from smt.api.app import create_app
from smt.db.models import Filing, PlannedSale, PlannedSaleMatch, Transaction
from smt.db.session import session_scope
from smt.ingest.form144 import (
    expire_unmatched,
    load_form144,
    match_sales_for,
    repair_notice_versions,
)
from smt.ingest.ownership import FilingMeta, load_ownership
from smt.sources.forms.form144 import parse_form144
from smt.sources.forms.ownership import PARSER_VERSION, parse_ownership
from smt.sources.forms.types import Form144Notice

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]
FIX = Path(__file__).resolve().parents[1] / "fixtures" / "edgar"

ORIGINAL = "0001959173-26-006819"
AMENDMENT = "TEST-144A-000001"
LATER_AMENDMENT = "TEST-144A-000002"
FORM4 = "0001821737-26-000013"
#: The sale (and the notice's approximate sale date) is 2026-09-09. A 144/A's own
#: matching window would open the day before it was filed, after the sale.
FILED_ON = {ORIGINAL: date(2026, 9, 9), AMENDMENT: date(2026, 9, 12), LATER_AMENDMENT: date(2026, 9, 14)}
ACCEPTED = {accession: datetime(d.year, d.month, d.day, 20, 0, tzinfo=UTC) for accession, d in FILED_ON.items()}


def _meta(accession: str, form: str, filed_on: date, accepted_at: datetime | None) -> FilingMeta:
    return FilingMeta(
        accession=accession, form_type=form, filed_on=filed_on, accepted_at=accepted_at,
        url=f"https://www.sec.gov/Archives/edgar/data/0/{accession.replace('-', '')}/{accession}-index.htm",
        primary_doc_url=None, detected_via="manual", parser_version=PARSER_VERSION, live=False,
    )


def _original() -> Form144Notice:
    return parse_form144((FIX / "form144" / f"{ORIGINAL}.xml").read_bytes())


def _amendment_of(amends: str) -> Form144Notice:
    """A test-only 144/A: the real notice restated, pointing back at `amends`."""
    return dataclasses.replace(_original(), submission_type="144/A", previous_accession=amends)


async def _load_notice(accession: str, filed_on: date | None = None) -> None:
    notice = _original() if accession == ORIGINAL else _amendment_of(ORIGINAL)
    form = "144" if accession == ORIGINAL else "144/A"
    meta = _meta(accession, form, filed_on or FILED_ON[accession], ACCEPTED[accession])
    async with session_scope() as session:
        await load_form144(session, meta, notice)


async def _load_form4_and_match() -> None:
    """What the pipeline does when a Form 4 with sales loads: retry the seller's open notices."""
    doc = parse_ownership((FIX / "ownership" / f"{FORM4}.xml").read_bytes())
    async with session_scope() as session:
        await load_ownership(session, _meta(FORM4, "4", date(2026, 9, 10), None), doc)
    async with session_scope() as session:
        seller, issuer = (await session.execute(
            select(PlannedSale.seller_entity_id, PlannedSale.issuer_entity_id).limit(1))).one()
        await match_sales_for(session, [seller], issuer)


class NoticeState(NamedTuple):
    superseded_by: str | None
    status: str
    matched_shares: Decimal | None
    linked_sales: int


async def _state() -> dict[str, NoticeState]:
    """Each loaded notice by accession: which filing replaced it, its status and its Form 4 links."""
    async with session_scope() as session:
        rows = (await session.execute(text("""
            SELECT f.source_key, s.source_key AS superseded_by, p.status, p.matched_shares,
                   (SELECT count(*) FROM planned_sale_matches m WHERE m.planned_sale_id = p.id) AS links
              FROM planned_sales p JOIN filings f ON f.id = p.filing_id
              LEFT JOIN filings s ON s.id = f.superseded_by_id"""))).all()
    return {r.source_key: NoticeState(r.superseded_by, r.status, r.matched_shares, int(r.links)) for r in rows}


async def _sale_id() -> int:
    async with session_scope() as session:
        return int((await session.execute(
            select(Transaction.id).where(Transaction.code == "S", Transaction.is_current.is_(True)))).scalar_one())


MATCHED = NoticeState(None, "matched", Decimal(50000), 1)


async def test_amendment_after_the_original_takes_over_its_match() -> None:
    await _load_notice(ORIGINAL)
    await _load_form4_and_match()
    assert (await _state())[ORIGINAL] == MATCHED

    await _load_notice(AMENDMENT)
    state = await _state()
    # The replaced notice keeps no link and no matched figures; the 144/A holds the sale,
    # although it was filed three days after it (its window opens with the original's).
    assert state[ORIGINAL] == NoticeState(AMENDMENT, "open", None, 0)
    assert state[AMENDMENT] == MATCHED


@pytest.mark.parametrize("form4_first", [True, False], ids=["form4-before-original", "form4-after-original"])
async def test_original_loading_after_its_amendment_is_superseded(form4_first: bool) -> None:
    # Gap filling: the 144/A is loaded first, the original days later.
    await _load_notice(AMENDMENT)
    if form4_first:
        await _load_form4_and_match()
    await _load_notice(ORIGINAL)
    if not form4_first:
        await _load_form4_and_match()

    state = await _state()
    assert state[ORIGINAL] == NoticeState(AMENDMENT, "open", None, 0)
    assert state[AMENDMENT] == MATCHED

    # Reloading either version (smt reprocess) changes nothing.
    await _load_notice(ORIGINAL)
    await _load_notice(AMENDMENT)
    assert await _state() == state


async def test_the_newest_of_two_amendments_is_the_current_version() -> None:
    # Two 144/As of an original that isn't loaded: the later one replaces the earlier one too.
    # Without the original's filing date, the window opens from the approximate sale date the
    # amendments restate (2026-09-09), so the sale is still found.
    for accession in (LATER_AMENDMENT, AMENDMENT):
        async with session_scope() as session:
            await load_form144(session, _meta(accession, "144/A", FILED_ON[accession], ACCEPTED[accession]),
                               _amendment_of(ORIGINAL))
    await _load_form4_and_match()

    state = await _state()
    assert state[AMENDMENT] == NoticeState(LATER_AMENDMENT, "open", None, 0)
    assert state[LATER_AMENDMENT] == MATCHED


async def test_an_amendment_keeps_its_originals_window() -> None:
    # An original filed three days after the sale (a late notice) doesn't reach back to it, and
    # its 144/A doesn't either: once the original is loaded its filing date opens the window,
    # whatever sale date the amendment restates.
    await _load_notice(ORIGINAL, filed_on=date(2026, 9, 12))
    await _load_notice(AMENDMENT, filed_on=date(2026, 9, 15))
    await _load_form4_and_match()

    state = await _state()
    assert state[ORIGINAL] == NoticeState(AMENDMENT, "open", None, 0)
    assert state[AMENDMENT] == NoticeState(None, "open", None, 0)


async def test_the_daily_sweep_moves_a_stale_link_to_the_current_version() -> None:
    from smt.sources.edgar.jobs import expire_planned_sales
    from smt.worker.scheduler import JobContext

    await _load_notice(ORIGINAL)
    await _load_notice(AMENDMENT)
    await _load_form4_and_match()
    assert (await _state())[AMENDMENT] == MATCHED

    # Recreate what loads before this fix left behind (planned sale 352 in the real data): the
    # replaced notice holds the sale and the 144/A is open with no links.
    sale_id = await _sale_id()
    async with session_scope() as session:
        ids = {r.source_key: r.id for r in (await session.execute(
            select(Filing.source_key, PlannedSale.id).join(PlannedSale, PlannedSale.filing_id == Filing.id))).all()}
        await session.execute(text("DELETE FROM planned_sale_matches"))
        session.add(PlannedSaleMatch(planned_sale_id=ids[ORIGINAL], transaction_id=sale_id, confidence=Decimal("0.9")))
        await session.execute(text(
            "UPDATE planned_sales SET status = 'matched', matched_shares = 50000, match_confidence = 0.9 "
            "WHERE id = :p"), {"p": ids[ORIGINAL]})
        await session.execute(text(
            "UPDATE planned_sales SET status = 'open', matched_shares = NULL, match_confidence = NULL "
            "WHERE id = :p"), {"p": ids[AMENDMENT]})
    assert (await _state())[AMENDMENT] == NoticeState(None, "open", None, 0)

    ctx = JobContext(job_name="edgar.expire_planned_sales", run_id=0, cursor={})
    await expire_planned_sales(ctx)
    state = await _state()
    assert ctx.stats["notice_families_repaired"] == 1
    assert state[ORIGINAL] == NoticeState(AMENDMENT, "open", None, 0)
    assert state[AMENDMENT] == MATCHED

    # Idempotent: a second run finds nothing to fix.
    async with session_scope() as session:
        assert await repair_notice_versions(session) == 0
    assert await _state() == state


async def test_the_sweep_links_an_original_that_was_never_marked_superseded() -> None:
    await _load_notice(AMENDMENT)
    await _load_notice(ORIGINAL)
    await _load_form4_and_match()
    sale_id = await _sale_id()
    # Before this fix a late original was never marked superseded, so it could take the sale.
    async with session_scope() as session:
        await session.execute(text("UPDATE filings SET superseded_by_id = NULL"))
        await session.execute(text("DELETE FROM planned_sale_matches"))
        await session.execute(text("UPDATE planned_sales SET status = 'open', matched_shares = NULL"))
        original_id = (await session.execute(select(PlannedSale.id).join(Filing, Filing.id == PlannedSale.filing_id)
                                             .where(Filing.source_key == ORIGINAL))).scalar_one()
        session.add(PlannedSaleMatch(planned_sale_id=original_id, transaction_id=sale_id, confidence=Decimal("0.9")))

    async with session_scope() as session:
        assert await repair_notice_versions(session) == 1
    state = await _state()
    assert state[ORIGINAL] == NoticeState(AMENDMENT, "open", None, 0)
    assert state[AMENDMENT] == MATCHED


async def test_expiry_judges_only_the_current_version() -> None:
    # Both versions filed long ago with no Form 4 loaded: only the 144/A is judged.
    await _load_notice(ORIGINAL, filed_on=date(2025, 1, 6))
    await _load_notice(AMENDMENT, filed_on=date(2025, 1, 7))
    async with session_scope() as session:
        assert await expire_unmatched(session) == 1
    state = await _state()
    assert state[AMENDMENT].status == "no_form4_found"
    assert state[ORIGINAL] == NoticeState(AMENDMENT, "open", None, 0)


async def test_pages_show_a_replaced_notice_through_its_amendment() -> None:
    # The Live event of the original, loaded before its 144/A (the usual order).
    await _load_notice(ORIGINAL)
    await _load_form4_and_match()
    await _load_notice(AMENDMENT)
    sale_id = await _sale_id()
    async with session_scope() as session:
        event_id = (await session.execute(text("SELECT id FROM feed_events WHERE event_key = :k"),
                                          {"k": f"edgar-144:{ORIGINAL}"})).scalar_one()

    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        profile = (await client.get("/api/tickers/SNOW")).json()
        detail = (await client.get(f"/api/feed/events/{event_id}")).json()

    # The replaced original is 'open' with no links, but it isn't an open notice: its
    # amendment stands, and that is matched.
    assert profile["planned_sales_open"] == 0
    # The original's quick look shows the notice as amended, with its Form 4 follow-up,
    # rather than "waiting for a Form 4".
    assert detail["accession"] == ORIGINAL
    assert detail["planned_sale"]["accession"] == AMENDMENT
    assert detail["planned_sale"]["status"] == "matched"
    assert [m["transaction_id"] for m in detail["matches"]] == [sale_id]
