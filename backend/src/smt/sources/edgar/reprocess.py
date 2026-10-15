"""Re-parse stored filings with the current parsers (`smt reprocess`).

Raw first, parse second: every EDGAR document is kept in the raw store before
it is parsed (smt.net.rawstore). When a parser or loader rule improves, this
rebuilds the affected filings from those stored bytes, with no new requests to
the SEC. Loading is idempotent (natural keys), so reprocessing a filing twice
changes nothing the second time; amendments keep their pairing (see
smt.ingest.ownership._pair_rows). Reprocessed filings never reach the Live feed.

Which filings:
* by default, those whose stored parser version differs from the current one
  (i.e. parsed by an older parser);
* `--all`: every stored filing;
* `--form 4` / `--accession ...`: narrow it down.

Filings that came only from the SEC's bulk data sets have no stored XML and are
not touched (the XML, when it is fetched, replaces them anyway).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import text

from smt.db.session import session_scope
from smt.ingest.form144 import load_form144
from smt.ingest.ownership import FilingMeta, load_ownership
from smt.net.rawstore import RawStore
from smt.sources.edgar.pipeline import FORM144_FORMS, OWNERSHIP_FORMS
from smt.sources.forms import form144 as form144_parser
from smt.sources.forms import ownership as ownership_parser
from smt.sources.forms.errors import ParseError

log = structlog.get_logger(__name__)


@dataclass
class ReprocessResult:
    filings: int = 0
    rows_new: int = 0
    rows_updated: int = 0
    failed: list[str] = field(default_factory=list)


def _current_version(form: str) -> str:
    return form144_parser.PARSER_VERSION if form in FORM144_FORMS else ownership_parser.PARSER_VERSION


async def select_filings(*, everything: bool = False, form: str | None = None,
                         accession: str | None = None, limit: int | None = None) -> list[Any]:
    """Stored primary documents to re-parse, oldest filing first."""
    where = ["r.doc_role = 'primary'", "r.source = 'edgar'", "f.form_type = ANY(:forms)"]
    params: dict[str, Any] = {"forms": sorted(OWNERSHIP_FORMS | FORM144_FORMS)}
    if form:
        params["forms"] = [form]
    if accession:
        where.append("f.source_key = :acc")
        params["acc"] = accession
    if not everything and not accession:
        # Only filings parsed by an older parser (or never parsed successfully).
        where.append("(r.parser_version IS DISTINCT FROM (CASE WHEN f.form_type IN ('144', '144/A') "
                     "THEN :v144 ELSE :vown END) OR r.parse_status IS DISTINCT FROM 'parsed')")
        params["v144"] = form144_parser.PARSER_VERSION
        params["vown"] = ownership_parser.PARSER_VERSION
    sql = ("SELECT DISTINCT ON (f.id) f.id, f.source_key, f.form_type, f.filed_on, f.accepted_at, f.url, "
           "f.detected_via, r.id AS raw_id, r.storage_path, r.url AS doc_url "
           "FROM raw_documents r JOIN filings f ON f.id = r.filing_id WHERE " + " AND ".join(where) +
           " ORDER BY f.id, r.fetched_at DESC")
    async with session_scope() as session:
        rows = list((await session.execute(text(sql), params)).all())
    rows.sort(key=lambda r: (r.filed_on is None, r.filed_on, r.id))
    return rows[:limit] if limit else rows


async def reprocess(*, everything: bool = False, form: str | None = None, accession: str | None = None,
                    limit: int | None = None, store: RawStore | None = None) -> ReprocessResult:
    """Re-parse and reload the selected filings, one transaction per filing."""
    store = store or RawStore()
    result = ReprocessResult()
    for f in await select_filings(everything=everything, form=form, accession=accession, limit=limit):
        try:
            content = store.get(f.storage_path)
            async with session_scope() as session:
                meta = FilingMeta(
                    accession=f.source_key, form_type=f.form_type, filed_on=f.filed_on, accepted_at=f.accepted_at,
                    url=f.url, primary_doc_url=f.doc_url, detected_via=f.detected_via,
                    parser_version=_current_version(f.form_type), raw_document_id=f.raw_id, live=False,
                )
                if f.form_type in FORM144_FORMS:
                    loaded = await load_form144(session, meta, form144_parser.parse_form144(content))
                else:
                    loaded = await load_ownership(session, meta, ownership_parser.parse_ownership(content))
            result.filings += 1
            result.rows_new += loaded.new_rows
            result.rows_updated += loaded.updated_rows
        except (ParseError, FileNotFoundError, ValueError) as exc:
            result.failed.append(f"{f.source_key}: {exc}"[:300])
            log.warning("reprocess.failed", accession=f.source_key, error=str(exc)[:300])
    return result
