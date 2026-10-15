"""Loading history from the SEC quarterly data set: precedence, idempotency, the nature defect.

The fixture zip holds the real 2026 Q2 rows of four filings (see
tests/fixtures/edgar/insider_dataset). Their XML is next to it, so the test can
check that the XML path wins.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select

from smt.db.models import FeedEvent, Filing, InsiderHolding, Transaction
from smt.db.session import session_scope
from smt.domain.business_days import ET
from smt.ingest.ownership import FilingMeta, load_ownership
from smt.sources.edgar.backfill import (
    PARSER_TAG,
    latest_published_quarter,
    load_quarter,
    quarters_to_load,
    trusted_nature,
)
from smt.sources.edgar.insider_dataset import iter_dataset_documents
from smt.sources.forms.ownership import PARSER_VERSION, parse_ownership

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "edgar" / "insider_dataset"
ZIP = FIX / "2026q2_form345_sample.zip"
WSM = "0000719955-26-000140"   # Form 3, holdings only; the SEC shifted its nature text
COCO = "0001482981-26-000122"  # all four row kinds


async def count(model: type) -> int:
    async with session_scope() as session:
        return int((await session.execute(select(func.count()).select_from(model))).scalar_one())


# ---------------------------------------------------------------- pure helpers (no database)


def test_quarter_resolution() -> None:
    mid_september = datetime(2026, 9, 24, 12, tzinfo=ET)
    assert latest_published_quarter(mid_september) == (2026, 2)
    # Early in a quarter the previous quarter's zip is not out yet.
    assert latest_published_quarter(datetime(2026, 10, 3, tzinfo=ET)) == (2026, 2)
    assert latest_published_quarter(datetime(2026, 10, 20, tzinfo=ET)) == (2026, 3)
    assert quarters_to_load(None, None, mid_september) == [(2026, 2), (2026, 1)]
    assert quarters_to_load(None, "2025q3", mid_september) == [(2026, 2), (2026, 1), (2025, 4), (2025, 3)]
    assert quarters_to_load(["2024q1", "2025Q4"], None, mid_september) == [(2025, 4), (2024, 1)]
    with pytest.raises(ValueError, match="not a quarter"):
        quarters_to_load(["2024-1"], None, mid_september)


def test_trusted_nature_blanks_only_mixed_groups() -> None:
    docs = {acc: doc for acc, _, doc in iter_dataset_documents(ZIP)}
    wsm = docs[WSM]
    before = [r.nature_of_ownership for r in wsm.rows]
    # The data set packs nature texts into the first rows of the group: a mix of set and missing.
    assert any(before)
    assert not all(before)
    after = trusted_nature(wsm)
    assert all(r.nature_of_ownership is None for r in after.rows)
    # The direct/indirect code is untouched.
    assert [r.direct_indirect for r in after.rows] == [r.direct_indirect for r in wsm.rows]
    # A document without mixed groups comes back unchanged (same object).
    unmixed = [d for d in docs.values()
               if not any(0 < sum(r.nature_of_ownership is not None for r in d.rows if (r.table, r.kind) == k)
                          < sum(1 for r in d.rows if (r.table, r.kind) == k)
                          for k in {(r.table, r.kind) for r in d.rows})]
    assert unmixed, "fixture should include at least one clean filing"
    assert trusted_nature(unmixed[0]) is unmixed[0]


# ---------------------------------------------------------------- loading


@pytest.mark.db
@pytest.mark.usefixtures("db")
async def test_load_quarter_is_idempotent_and_never_live() -> None:
    first = await load_quarter(ZIP)
    assert first["filings"] == 4
    assert first["quarantined"] == 0
    assert first["nature_blanked"] >= 1
    tx, holdings = await count(Transaction), await count(InsiderHolding)
    assert tx > 0
    assert holdings > 0
    # Backfill events exist (ticker timelines use them) but never reach the Live page.
    async with session_scope() as session:
        live_flags = set((await session.execute(select(FeedEvent.is_live))).scalars())
    assert live_flags == {False}

    async with session_scope() as session:
        tags = set((await session.execute(select(Filing.parser_version))).scalars())
        via = set((await session.execute(select(Filing.detected_via))).scalars())
    assert tags == {PARSER_TAG}
    assert via == {"backfill"}

    second = await load_quarter(ZIP)
    assert second["filings"] == 4
    assert (await count(Transaction), await count(InsiderHolding)) == (tx, holdings)


@pytest.mark.db
@pytest.mark.usefixtures("db")
async def test_xml_wins_over_the_data_set() -> None:
    # COCO arrives first through the live XML path...
    doc = parse_ownership((FIX / f"{COCO}.xml").read_bytes())
    meta = FilingMeta(accession=COCO, form_type=doc.document_type, filed_on=date(2026, 5, 1), accepted_at=None,
                      url="https://www.sec.gov/", primary_doc_url=None, detected_via="feed",
                      parser_version=PARSER_VERSION, live=False)
    async with session_scope() as session:
        await load_ownership(session, meta, doc)
        xml_prices = sorted(str(p) for p in (await session.execute(select(Transaction.price))).scalars()
                            if p is not None)

    # ...so the backfill skips it and leaves the full-precision prices alone.
    stats = await load_quarter(ZIP)
    assert stats["skipped_xml"] == 1
    assert stats["filings"] == 3
    async with session_scope() as session:
        coco = (await session.execute(select(Filing).where(Filing.source_key == COCO))).scalar_one()
        assert coco.parser_version == PARSER_VERSION
        prices = sorted(str(p) for p in (await session.execute(
            select(Transaction.price).where(Transaction.filing_id == coco.id))).scalars() if p is not None)
    assert prices == xml_prices


@pytest.mark.db
@pytest.mark.usefixtures("db")
async def test_later_xml_overwrites_a_backfilled_filing() -> None:
    await load_quarter(ZIP)
    doc = parse_ownership((FIX / f"{WSM}.xml").read_bytes())
    meta = FilingMeta(accession=WSM, form_type=doc.document_type, filed_on=date(2026, 5, 1), accepted_at=None,
                      url="https://www.sec.gov/", primary_doc_url=None, detected_via="daily_index",
                      parser_version=PARSER_VERSION, live=False)
    async with session_scope() as session:
        await load_ownership(session, meta, doc)
    async with session_scope() as session:
        wsm = (await session.execute(select(Filing).where(Filing.source_key == WSM))).scalar_one()
        assert wsm.parser_version == PARSER_VERSION
        natures = {(h.table_name, h.line_no): h.nature_text for h in (await session.execute(
            select(InsiderHolding).where(InsiderHolding.filing_id == wsm.id))).scalars()}
    # The XML's nature texts are back, on the right rows.
    expected = {(r.table, r.line_no): r.nature_of_ownership for r in doc.rows if r.kind == "holding"}
    assert natures == expected
    assert any(expected.values())


@pytest.mark.db
@pytest.mark.usefixtures("db")
async def test_quarters_loaded_by_this_reader_version_are_skipped() -> None:
    from sqlalchemy import insert

    from smt.db.models import IngestionRun
    from smt.sources.edgar.backfill import quarters_already_loaded
    from smt.sources.edgar.insider_dataset import READER_VERSION

    async with session_scope() as session:
        await session.execute(insert(IngestionRun).values([
            {"job": "edgar.backfill_insider_dataset", "source": "edgar", "status": "ok",
             "stats": {"quarter": "2026q2", "reader_version": READER_VERSION}},
            # A failed run, or one by an older reader, doesn't count as loaded.
            {"job": "edgar.backfill_insider_dataset", "source": "edgar", "status": "failed",
             "stats": {"quarter": "2026q1", "reader_version": READER_VERSION}},
            {"job": "edgar.backfill_insider_dataset", "source": "edgar", "status": "ok",
             "stats": {"quarter": "2025q4", "reader_version": "0.9.0"}},
        ]))
    assert await quarters_already_loaded(["2026q2", "2026q1", "2025q4", "2025q3"]) == {"2026q2"}
    assert await quarters_already_loaded([]) == set()
