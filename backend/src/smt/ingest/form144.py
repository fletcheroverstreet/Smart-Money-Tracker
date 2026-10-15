"""Load a parsed Form 144 (notice of proposed sale) and link it to the sales that followed.

A Form 144 says an affiliate *intends* to sell. It is not a sale. The UI
shows "intends to sell ~$X". When a later Form 4 reports the sale, it's
linked as "likely executed by", with a confidence score.

Matching rules (PLAN.md §6.4):
* same seller (by entity) and same issuer;
* Form 4 rows with code S only;
* traded from one day before the 144's filing date to 90 days after
  (a 144 covers sales within about three months). For a 144/A the window
  opens one day before the original notice was filed, since the amendment
  restates a notice given earlier and is often filed after the sale;
* the running total of matched shares is compared with the planned shares,
  because one planned sale is often executed across several Form 4 rows.

Many Form 144 filers are affiliates who never file a Form 4. So "no matching
Form 4 found" never means "didn't sell".

Amendments (144/A). A 144/A restates the whole notice it amends, so only the
newest version of a notice counts and only it holds Form 4 links. Filings can
load in any order (gap filling often loads an original after its 144/A), so
every load settles the notice's whole family of versions, and the daily
expiry job sweeps up any family left inconsistent (settle_notice_versions,
repair_notice_versions).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Row, and_, delete, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from smt.db.models import (
    Filing,
    FilingParty,
    PlannedSale,
    PlannedSaleMatch,
    Security,
    Transaction,
    TransactionOwner,
)
from smt.domain.names import pad_cik
from smt.ingest.entities import ensure_filer
from smt.ingest.feed import upsert_feed_event
from smt.ingest.ownership import FilingMeta, LoadResult
from smt.sources.forms.types import Form144Notice

MATCH_WINDOW_DAYS = 90
#: The form types of a notice and of its amendment.
FORM144_TYPES = ("144", "144/A")
#: Sorts a filing with no acceptance time before every real one.
_NEVER = datetime.min.replace(tzinfo=UTC)


async def load_form144(session: AsyncSession, meta: FilingMeta, notice: Form144Notice) -> LoadResult:
    """Load one Form 144 notice. Idempotent."""
    # Name the seller from EDGAR's conformed name when the feed or daily index gave
    # it; the notice's own name field is free text in no fixed word order.
    conformed = meta.filer_names.get(pad_cik(notice.filer_cik))
    seller = await ensure_filer(session, notice.filer_cik, conformed or notice.person_name or notice.filer_cik,
                                tags=("form144_filer",), trusted_name=conformed is not None)
    issuer = None
    security_id: int | None = None
    ticker: str | None = None
    if notice.issuer_cik:
        issuer = await ensure_filer(session, notice.issuer_cik, notice.issuer_name or notice.issuer_cik,
                                    kind_hint="company", tags=("issuer",))
        found = (await session.execute(
            select(Security.id, Security.primary_ticker).where(Security.issuer_entity_id == issuer.id)
            .order_by(Security.id)
        )).first()
        if found:
            security_id, ticker = found.id, found.primary_ticker

    values: dict[str, Any] = {
        "source": "edgar", "source_key": meta.accession, "form_type": meta.form_type,
        "accepted_at": meta.accepted_at, "filed_on": meta.filed_on, "period_of_report": None,
        "issuer_entity_id": issuer.id if issuer else None, "url": meta.url,
        "primary_doc_url": meta.primary_doc_url, "is_amendment": notice.submission_type.endswith("/A"),
        "machine_readable": True, "remarks": notice.remarks, "detected_via": meta.detected_via,
        "parser_version": meta.parser_version,
        "flags": {"previous_accession": notice.previous_accession},
    }
    stmt = insert(Filing).values(**values)
    upsert = stmt.on_conflict_do_update(
        index_elements=["source", "source_key"],
        set_={k: stmt.excluded[k] for k in values if k not in ("source", "source_key", "detected_via")}
        | {"updated_at": text("now()")},
    ).returning(Filing.id)
    filing_id = int((await session.execute(upsert)).scalar_one())
    result = LoadResult(filing_id=filing_id)

    if meta.raw_document_id is not None:
        await session.execute(text(
            "UPDATE raw_documents SET filing_id = :f, parse_status = 'parsed', parser_name = 'form144', "
            "parser_version = :v, parse_error = NULL WHERE id = :id"),
            {"f": filing_id, "v": meta.parser_version, "id": meta.raw_document_id})

    for role, ref in (("filer", seller), ("subject_company", issuer)):
        if ref is not None:
            await session.execute(insert(FilingParty).values(
                filing_id=filing_id, entity_id=ref.id, role=role, is_primary=(role == "filer"),
                relationship={"relationships": list(notice.relationships)} if role == "filer" else {},
            ).on_conflict_do_nothing())

    shares = _sum([s.units_to_sell for s in notice.securities])
    market_value = _sum([s.aggregate_market_value for s in notice.securities])
    sale_dates = [s.approx_sale_date for s in notice.securities if s.approx_sale_date]
    first = notice.securities[0] if notice.securities else None
    ps_values: dict[str, Any] = {
        "filing_id": filing_id, "source": "form144", "seller_entity_id": seller.id,
        "issuer_entity_id": issuer.id if issuer else None, "security_id": security_id,
        "security_title": first.class_title if first else None,
        "relationships": list(notice.relationships), "shares": shares, "market_value": market_value,
        "shares_outstanding": first.units_outstanding if first else None,
        "approx_sale_on": min(sale_dates) if sale_dates else None,
        "broker": first.broker_name if first else None, "exchange": first.exchange if first else None,
        "plan_adoption_dates": list(notice.plan_adoption_dates),
        "details": {
            "securities": [s.__dict__ for s in notice.securities],
            "acquisitions": [a.__dict__ for a in notice.acquisitions],
            "past_sales": [p.__dict__ for p in notice.past_sales],
            "nothing_sold_past_3_months": notice.nothing_sold_past_3_months,
            "notice_date": notice.notice_date,
        },
    }
    ps = insert(PlannedSale).values(**_jsonable_details(ps_values))
    ps_upsert = ps.on_conflict_do_update(
        index_elements=["filing_id"], set_={k: ps.excluded[k] for k in ps_values if k != "filing_id"},
    ).returning(text("(xmax = 0) AS inserted"))
    inserted = (await session.execute(ps_upsert)).scalar_one()
    if inserted:
        result.new_rows += 1
    else:
        result.updated_rows += 1

    # A 144/A replaces the notice it amends, and either can arrive first. Work out which
    # version of this notice is current, then re-match every version: superseded ones drop
    # their Form 4 links and the current one is matched afresh.
    await settle_notice_versions(session, filing_id)

    event_id = await upsert_feed_event(
        session, event_key=f"edgar-144:{meta.accession}", event_type="planned_sale", source="edgar",
        form_type=meta.form_type, section="people", filing_id=filing_id, entity_id=seller.id,
        security_id=security_id, size_usd=float(market_value) if market_value is not None else None,
        occurred_on=ps_values["approx_sale_on"], disclosed_at=meta.accepted_at, is_live=meta.live,
        payload={
            "accession": meta.accession, "form": meta.form_type, "filed_on": meta.filed_on,
            "accepted_at": meta.accepted_at, "url": meta.url,
            "owner": {"name": seller.name, "slug": seller.slug, "kind": seller.kind,
                      "role": ", ".join(notice.relationships) or "Affiliate"},
            "issuer": {"name": issuer.name, "slug": issuer.slug} if issuer else None,
            "ticker": ticker,
            "shares": shares, "market_value": market_value, "approx_sale_on": ps_values["approx_sale_on"],
            "security_title": ps_values["security_title"], "is_10b5_1": bool(notice.plan_adoption_dates),
        },
    )
    if event_id:
        result.feed_event_ids.append(event_id)
    return result


def _sum(values: list[Decimal | None]) -> Decimal | None:
    present = [v for v in values if v is not None]
    return sum(present, Decimal(0)) if present else None


def _jsonable_details(values: dict[str, Any]) -> dict[str, Any]:
    from smt.ingest.feed import _jsonable

    return values | {"details": _jsonable(values["details"])}


async def match_planned_sale(session: AsyncSession, planned_sale_id: int) -> None:
    """(Re)match one planned sale against Form 4 sale rows. Safe to call repeatedly.

    Each Form 4 sale is linked to at most one notice: sales already linked to
    another current notice are skipped. Candidates are taken closest to the
    notice's approximate sale date first, and their shares added up until they
    reach the planned shares; the matched total reported never exceeds the
    plan. A notice replaced by a 144/A keeps no links (the amendment matches
    instead). Previous links are cleared first, so a re-run reflects the rules.
    """
    # populate_existing: re-read both rows, because a bulk upsert or update earlier in this
    # session (a reload of the notice, a change of which version is current) may have
    # changed them behind the session's cached copies.
    sale = await session.get(PlannedSale, planned_sale_id, populate_existing=True)
    if sale is None:
        return
    await session.execute(delete(PlannedSaleMatch).where(PlannedSaleMatch.planned_sale_id == sale.id))
    filing = await session.get(Filing, sale.filing_id, populate_existing=True)
    if (sale.issuer_entity_id is None or filing is None or filing.filed_on is None
            or filing.superseded_by_id is not None):
        sale.matched_shares, sale.match_confidence = None, None
        if sale.status == "matched":
            sale.status = "open"
        return
    # The window opens one day before the notice was first filed. A 144/A restates a notice
    # given earlier; it is usually filed days later, often after the sale itself, so its
    # window opens from the original's date, not its own (see _notice_first_filed).
    first_filed = await _notice_first_filed(session, sale, filing, filing.filed_on)
    start = first_filed - timedelta(days=1)
    # It closes 90 days after this version was filed. The current version is the newest
    # filing of its notice, so an amendment can only lengthen the window. expire_unmatched
    # judges a notice against this same end date.
    end = filing.filed_on + timedelta(days=MATCH_WINDOW_DAYS)
    taken_elsewhere = (
        select(PlannedSaleMatch.transaction_id)
        .join(PlannedSale, PlannedSale.id == PlannedSaleMatch.planned_sale_id)
        .join(Filing, Filing.id == PlannedSale.filing_id)
        .where(PlannedSaleMatch.planned_sale_id != sale.id, Filing.superseded_by_id.is_(None))
    )
    candidates = (await session.execute(
        select(Transaction.id, Transaction.shares, Transaction.traded_on)
        .outerjoin(TransactionOwner, TransactionOwner.transaction_id == Transaction.id)
        .where(
            Transaction.issuer_entity_id == sale.issuer_entity_id, Transaction.code == "S",
            Transaction.is_current.is_(True), Transaction.is_derivative.is_(False),
            Transaction.duplicate_of_id.is_(None), Transaction.traded_on.between(start, end),
            or_(Transaction.entity_id == sale.seller_entity_id,
                TransactionOwner.entity_id == sale.seller_entity_id),
            Transaction.id.not_in(taken_elsewhere),
        ).distinct()
    )).all()
    if not candidates:
        sale.matched_shares, sale.match_confidence = None, None
        if sale.status == "matched":
            sale.status = "open"
        return
    anchor = sale.approx_sale_on or first_filed
    ordered = sorted(candidates, key=lambda c: (abs((c.traded_on - anchor).days), c.traded_on, c.id))
    planned = sale.shares
    total = Decimal(0)
    for tx_id, shares, traded_on in ordered:
        if planned is not None and total >= planned:
            break
        total += shares or Decimal(0)
        near = sale.approx_sale_on is not None and abs((traded_on - sale.approx_sale_on).days) <= 3
        confidence = Decimal("0.90") if near else Decimal("0.70")
        await session.execute(insert(PlannedSaleMatch).values(
            planned_sale_id=sale.id, transaction_id=tx_id, confidence=confidence).on_conflict_do_nothing())
    # The last sale can take the running total past the plan; report no more than was planned.
    sale.matched_shares = min(total, planned) if planned is not None else total
    sale.status = "matched"
    sale.match_confidence = Decimal("0.90") if planned is not None and total == planned else Decimal("0.70")


async def _notice_first_filed(session: AsyncSession, sale: PlannedSale, filing: Filing, filed_on: date) -> date:
    """The day the notice was first filed, where its matching window opens.

    An original notice: its own filing date (`filed_on`). A 144/A: the
    earliest filing date among the notice's loaded versions, which is the
    original's when the original is loaded. When it isn't (it hasn't loaded
    yet, or was never filed on EDGAR), the approximate sale date the amendment
    restates counts too, if it is earlier: a Form 144 is filed when the sale
    order is placed, so that date stands in for the missing original's filing
    date. With the original loaded, its filing date decides, exactly as it
    does for the original itself.
    """
    if not filing.is_amendment:
        return filed_on
    family = await _notice_family(session, filing.id)
    filed_dates: list[date] = [v.filed_on for v in family if v.filed_on is not None]
    earliest = min([*filed_dates, filed_on])
    original_loaded = any(not v.is_amendment for v in family)
    if original_loaded or sale.approx_sale_on is None:
        return earliest
    return min(earliest, sale.approx_sale_on)


async def match_sales_for(session: AsyncSession, seller_entity_ids: list[int], issuer_entity_id: int) -> None:
    """After new Form 4 sales load, retry matching open planned sales for those sellers."""
    open_sales = (await session.execute(
        select(PlannedSale.id).where(
            PlannedSale.issuer_entity_id == issuer_entity_id,
            PlannedSale.seller_entity_id.in_(seller_entity_ids),
            PlannedSale.status.in_(("open", "matched")),
        )
    )).scalars().all()
    for planned_id in open_sales:
        await match_planned_sale(session, int(planned_id))


async def settle_notice_versions(session: AsyncSession, filing_id: int, *, rematch: bool = True) -> bool:
    """Make the newest version of a Form 144 notice the current one, and re-match its versions.

    EDGAR links a 144/A to the notice it amends only one way: the amendment
    names the accession it amends. Either can load first, so this collects the
    whole family (the original plus every amendment pointing back to it,
    directly or through another amendment), then:

    * the newest amendment becomes the current version; every other version is
      marked superseded by it (a 144/A restates the whole notice, so an older
      version's figures no longer stand);
    * superseded versions are re-matched first, which clears their Form 4 links
      and figures (see match_planned_sale), and then the current version is
      matched, so a sale linked to an old version moves to the current one.

    ``rematch=True`` (used at load) re-matches every version even when nothing
    else changed. ``rematch=False`` (the daily sweep) touches a family only when
    it is inconsistent: a version marked wrongly, or a superseded version still
    holding links. Returns True when the family was changed or re-matched.
    """
    family = await _notice_family(session, filing_id)
    return await _settle_family(session, family, rematch=rematch) if family else False


async def repair_notice_versions(session: AsyncSession) -> int:
    """Settle every Form 144 notice family left inconsistent. Returns how many families were fixed.

    Part of the daily expiry job, and also the one-off repair for notices
    loaded before versions were settled at load time (for example an original
    that loaded after its 144/A and kept a Form 4 link its amendment should
    hold). Only families with an amendment or a superseded version are looked
    at. Idempotent: a second run finds nothing to fix and returns 0.
    """
    candidates = (await session.execute(
        select(Filing.id).where(
            Filing.source == "edgar", Filing.form_type.in_(FORM144_TYPES),
            or_(Filing.flags["previous_accession"].astext.is_not(None), Filing.superseded_by_id.is_not(None)),
        ).order_by(Filing.id)
    )).scalars().all()
    repaired = 0
    done: set[int] = set()
    for filing_id in candidates:
        if filing_id in done:
            continue  # already settled as part of an earlier family
        family = await _notice_family(session, int(filing_id))
        done.update(version.id for version in family)
        if family and await _settle_family(session, family, rematch=False):
            repaired += 1
    return repaired


async def _notice_family(session: AsyncSession, filing_id: int) -> list[Row[Any]]:
    """Every loaded version of the notice that `filing_id` belongs to (the original and its amendments).

    First walks up the "amends" links to the root accession (the original
    notice, which may not be loaded yet), then collects every Form 144 filing
    that is the root or amends a family member. The `seen`/`known` sets stop a
    malformed loop of links.
    """
    columns = (Filing.id, Filing.source_key, Filing.is_amendment, Filing.accepted_at, Filing.filed_on,
               Filing.superseded_by_id, Filing.flags["previous_accession"].astext.label("previous_accession"))
    start = (await session.execute(
        select(*columns).where(Filing.id == filing_id, Filing.form_type.in_(FORM144_TYPES))
    )).first()
    if start is None:
        return []
    root: str = start.source_key
    amends: str | None = start.previous_accession
    seen = {root}
    while amends and amends not in seen:
        seen.add(amends)
        root = amends
        amends = (await session.execute(
            select(Filing.flags["previous_accession"].astext).where(
                Filing.source == "edgar", Filing.source_key == root, Filing.form_type.in_(FORM144_TYPES))
        )).scalar_one_or_none()

    members: dict[int, Row[Any]] = {}
    frontier, known = {root}, {root}
    while frontier:
        rows = (await session.execute(
            select(*columns).where(
                Filing.source == "edgar", Filing.form_type.in_(FORM144_TYPES),
                or_(Filing.source_key.in_(frontier), Filing.flags["previous_accession"].astext.in_(frontier)))
        )).all()
        members.update({row.id: row for row in rows})
        frontier = {row.source_key for row in rows} - known
        known |= frontier
    return list(members.values())


def _current_version(family: Sequence[Row[Any]]) -> Row[Any]:
    """The version that stands: the latest-accepted 144/A, or the original when nothing amends it."""
    amendments = [version for version in family if version.is_amendment]
    return max(amendments or family,
               key=lambda v: (v.accepted_at or _NEVER, v.filed_on or date.min, v.id))


async def _settle_family(session: AsyncSession, family: Sequence[Row[Any]], *, rematch: bool) -> bool:
    """Mark the family's versions current or superseded, and re-match them. See settle_notice_versions."""
    current = _current_version(family)
    wrong = [v for v in family if v.superseded_by_id != (None if v.id == current.id else current.id)]
    superseded_ids = [v.id for v in family if v.id != current.id]
    stale_links = bool(superseded_ids) and (await session.execute(
        select(PlannedSaleMatch.planned_sale_id)
        .join(PlannedSale, PlannedSale.id == PlannedSaleMatch.planned_sale_id)
        .where(PlannedSale.filing_id.in_(superseded_ids)).limit(1)
    )).first() is not None
    if not (rematch or wrong or stale_links):
        return False
    for version in wrong:
        await session.execute(update(Filing).where(Filing.id == version.id).values(
            superseded_by_id=None if version.id == current.id else current.id))
    planned = (await session.execute(
        select(PlannedSale.id, PlannedSale.filing_id).where(PlannedSale.filing_id.in_([v.id for v in family]))
    )).all()
    # Superseded versions first (False sorts before True), so their links are released
    # before the current version looks for sales.
    for row in sorted(planned, key=lambda p: (p.filing_id == current.id, p.id)):
        await match_planned_sale(session, int(row.id))
    return True


async def expire_unmatched(session: AsyncSession) -> int:
    """Mark notices with no matching Form 4 after the window as 'no_form4_found' (not 'didn't sell').

    Only current notices are judged: a notice replaced by a 144/A is judged
    through its amendment. The judgement waits until the matching window has
    closed, 90 days after the current version was filed (see match_planned_sale).
    A superseded notice keeps status 'open' with no links, so every reader of
    planned_sales filters on filings.superseded_by_id IS NULL.
    """
    rows = await session.execute(
        update(PlannedSale).where(
            PlannedSale.status == "open",
            PlannedSale.filing_id.in_(select(Filing.id).where(
                and_(Filing.filed_on.is_not(None), Filing.superseded_by_id.is_(None),
                     Filing.filed_on < text(f"current_date - interval '{MATCH_WINDOW_DAYS} days'")))),
        ).values(status="no_form4_found")
    )
    return int(rows.rowcount or 0)  # type: ignore[attr-defined]
