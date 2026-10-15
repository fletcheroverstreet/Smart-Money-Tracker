"""One trade in full: what the quick-look drawer shows, with a link to the original filing."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import text

from smt.api.deps import Session
from smt.api.queries import TRADE_SQL, entity_link, trade_row
from smt.api.schemas import FootnoteOut, TradeCopy, TradeDetail

router = APIRouter(prefix="/api/trades", tags=["trades"])


@router.get("/{trade_id}")
async def trade_detail(session: Session, trade_id: int, response: Response) -> TradeDetail:
    """One trade for the quick look; `trade.in_totals` follows the site-wide rule (COUNTED)."""
    row = (await session.execute(text(TRADE_SQL.format(where="t.id = :id", order="t.id")),
                                  {"id": trade_id, "limit": 1})).first()
    if row is None:
        raise HTTPException(status_code=404, detail="No such trade")
    extra = (await session.execute(text("""
        SELECT t.footnote_ids, t.superseded_by_id, t.supersedes_id, t.review_note, f.footnotes, f.remarks,
               f.primary_doc_url, f.parser_version
        FROM transactions t JOIN filings f ON f.id = t.filing_id WHERE t.id = :id"""), {"id": trade_id})).one()
    owners = (await session.execute(text("""
        SELECT e.name, e.slug, e.kind FROM transaction_owners o JOIN entities e ON e.id = o.entity_id
        WHERE o.transaction_id = :id ORDER BY o.is_primary DESC, e.sort_name"""), {"id": trade_id})).all()
    # Other filings' rows for this same trade (a fund and its general partner, or the same trade
    # filed again) point at this row; totals count the trade once (smt.ingest.quality.mark_duplicates).
    # A copy can point here through another copy (see smt.api.queries.COUNTED_COPY), so walk the
    # pointers down; rows only ever point at lower ids, and the depth cap is a safety net.
    copies = (await session.execute(text("""
        WITH RECURSIVE below(id, depth) AS (
            SELECT c.id, 1 FROM transactions c WHERE c.duplicate_of_id = :id
            UNION ALL
            SELECT c.id, b.depth + 1 FROM below b JOIN transactions c ON c.duplicate_of_id = b.id
             WHERE b.depth < 20
        )
        SELECT c.id, e.name, e.slug, e.kind
          FROM below b JOIN transactions c ON c.id = b.id JOIN entities e ON e.id = c.entity_id
         WHERE c.is_current ORDER BY c.id"""), {"id": trade_id})).all()
    notes = extra.footnotes or {}
    response.headers["Cache-Control"] = "public, max-age=300"
    return TradeDetail(
        trade=trade_row(row),
        owners=[entity_link(o.name, o.slug, o.kind) for o in owners],
        footnotes=[FootnoteOut(id=fid, text=str(notes.get(fid, ""))) for fid in (extra.footnote_ids or [])
                   if fid in notes],
        remarks=extra.remarks, raw_document_url=extra.primary_doc_url,
        superseded_by=extra.superseded_by_id, supersedes=extra.supersedes_id, review_note=extra.review_note,
        source_note=_source_note(extra.parser_version),
        copies=[TradeCopy(trade_id=c.id, owner=entity_link(c.name, c.slug, c.kind)) for c in copies],
    )


def _source_note(parser_version: str | None) -> str | None:
    """Say so when a trade came from the SEC's bulk data set rather than the filing's own XML."""
    if parser_version and parser_version.startswith("dataset/"):
        return ("Loaded from the SEC's quarterly Insider Transactions data set, which rounds prices and "
                "values to the cent. The original filing has the exact figures.")
    return None
