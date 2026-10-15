"""Historical Forms 3/4/5 from the SEC's quarterly Insider Transactions Data Sets.

`smt backfill` downloads one zip per quarter, keeps it in the raw store, reads
it with `iter_dataset_documents` and loads every filing through the same
`load_ownership` the live pipeline uses, so natural keys are identical and a
filing seen by both paths is stored once.

Which path wins. The live XML is the better copy, because the data set:

* rounds every number to 2 decimals (a $0.005 exercise price becomes $0.01),
* shifts "nature of ownership" text onto the wrong rows whenever only some
  rows of a table have it (an SEC defect, see `insider_dataset.py`).

So:

1. A filing already loaded from its XML is skipped here.
2. A filing loaded from the data set is overwritten when its XML is later
   processed (the loader upserts), and its parser_version changes from
   ``dataset/...`` to the XML parser's version.
3. Nature-of-ownership text is kept only where it is trustworthy: when every
   row of a (table, kind) group has one, or none do. In a mixed group we
   cannot tell which row a value belongs to, so it is stored as missing
   rather than risk attaching "By Family Trust" to the wrong line.
4. Filings from the data set are marked (parser_version ``dataset/<ver>``) so
   the site can say "prices rounded to the cent by the SEC data set".

Backfilled rows never enter the Live feed (``live=False``).
"""

from __future__ import annotations

import dataclasses
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import structlog
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert

from smt.config import get_settings
from smt.db.models import Filing, IngestionRun, Quarantine, RawDocument
from smt.db.session import session_scope
from smt.domain.business_days import ET
from smt.ingest.ownership import FilingMeta, filing_index_url, load_ownership
from smt.net.client import Fetched, HttpClient, shared_client
from smt.net.ratelimit import Priority
from smt.net.rawstore import RawStore
from smt.sources.edgar.insider_dataset import READER_VERSION, iter_dataset_documents
from smt.sources.forms.errors import ParseError
from smt.sources.forms.types import OwnershipDocument, OwnershipRow

log = structlog.get_logger(__name__)

PARSER_TAG = f"dataset/{READER_VERSION}"

# The SEC moved new zips to a new folder in 2026; older quarters stay in the old one.
_NEW_BASE = "https://www.sec.gov/files/datastandardsinnovation/data/insider-transactions-data-sets"
_OLD_BASE = "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets"

_QUARTER_RE = re.compile(r"^(\d{4})q([1-4])$")

# Commit every N filings, so a crash loses at most one batch (a rerun redoes it idempotently).
_COMMIT_EVERY = 500


def parse_quarter(text_value: str) -> tuple[int, int]:
    """'2026q2' -> (2026, 2). Raises ValueError for anything else."""
    match = _QUARTER_RE.match(text_value.strip().lower())
    if not match:
        raise ValueError(f"not a quarter: {text_value!r} (expected e.g. 2026q2)")
    return int(match.group(1)), int(match.group(2))


def latest_published_quarter(now: datetime | None = None) -> tuple[int, int]:
    """The newest quarter the SEC has published.

    A quarter's zip appears in the first weeks after it ends (2026 Q2 was
    posted 9 July), so until the 15th of the quarter's first month we assume
    the previous one is the newest.
    """
    today = (now or datetime.now(ET)).date()
    year, quarter = today.year, (today.month - 1) // 3 + 1
    year, quarter = previous_quarter(year, quarter)  # the running quarter is never published
    if today.month in (1, 4, 7, 10) and today.day < 15:
        year, quarter = previous_quarter(year, quarter)
    return year, quarter


def previous_quarter(year: int, quarter: int) -> tuple[int, int]:
    return (year - 1, 4) if quarter == 1 else (year, quarter - 1)


def quarters_to_load(quarters: list[str] | None, since: str | None, now: datetime | None = None) -> list[tuple[int, int]]:
    """Resolve the CLI's --quarters / --since into a newest-first list."""
    newest = latest_published_quarter(now)
    if quarters:
        return sorted({parse_quarter(q) for q in quarters}, reverse=True)
    if since:
        start = parse_quarter(since)
        out: list[tuple[int, int]] = []
        current = newest
        while current >= start:
            out.append(current)
            current = previous_quarter(*current)
        return out
    # Default (PROGRESS.md "Decisions in force"): the latest two quarters locally.
    return [newest, previous_quarter(*newest)]


def dataset_urls(year: int, quarter: int) -> list[str]:
    name = f"{year}q{quarter}_form345.zip"
    return [f"{_NEW_BASE}/{name}", f"{_OLD_BASE}/{name}"]


def trusted_nature(doc: OwnershipDocument) -> OwnershipDocument:
    """Blank nature-of-ownership text in (table, kind) groups where the SEC may have shifted it.

    See the module docstring, point 3. The row's direct/indirect code ("D"/"I")
    is not affected by the defect and is kept.
    """
    groups: dict[tuple[str, str], list[OwnershipRow]] = defaultdict(list)
    for row in doc.rows:
        groups[(row.table, row.kind)].append(row)
    mixed = {key for key, rows in groups.items()
             if 0 < sum(r.nature_of_ownership is not None for r in rows) < len(rows)}
    if not mixed:
        return doc
    rows = tuple(
        dataclasses.replace(r, nature_of_ownership=None) if (r.table, r.kind) in mixed else r
        for r in doc.rows
    )
    return dataclasses.replace(doc, rows=rows)


async def _download(client: HttpClient, store: RawStore, year: int, quarter: int) -> tuple[Path, int]:
    """Fetch one quarter's zip (raw-first: stored before it is read). Returns (path, raw_document_id)."""
    last: Fetched | None = None
    for url in dataset_urls(year, quarter):
        fetched = await client.get(url, priority=Priority.BACKFILL)
        if fetched.ok:
            sha, rel = store.put(fetched.content)
            async with session_scope() as session:
                raw_id = int((await session.execute(
                    insert(RawDocument).values(
                        source="edgar", url=fetched.url, sha256=sha, http_status=fetched.status,
                        etag=fetched.header("etag"), last_modified=fetched.header("last-modified"),
                        content_type=fetched.header("content-type"), byte_size=len(fetched.content),
                        storage_path=rel, doc_role="insider_dataset",
                    ).on_conflict_do_update(index_elements=["url", "sha256"], set_={"fetched_at": text("now()")})
                    .returning(RawDocument.id)
                )).scalar_one())
            # The reader needs a real .zip file; write a plain copy next to the cache.
            cache = get_settings().raw_store_dir.parent / "cache"
            cache.mkdir(parents=True, exist_ok=True)
            path = cache / f"{year}q{quarter}_form345.zip"
            if not path.exists() or path.stat().st_size != len(fetched.content):
                path.write_bytes(fetched.content)
            return path, raw_id
        last = fetched
    status = last.status if last else "no response"
    raise FileNotFoundError(f"{year}q{quarter}: data set not found at the SEC (HTTP {status})")


async def _xml_loaded_accessions() -> set[str]:
    """Accessions already loaded from their XML; the data set must not overwrite them."""
    async with session_scope() as session:
        rows = await session.execute(
            select(Filing.source_key).where(
                Filing.source == "edgar", Filing.form_type.in_(("3", "3/A", "4", "4/A", "5", "5/A")),
                ~Filing.parser_version.like("dataset/%"),
            )
        )
        return {str(r[0]) for r in rows}


async def load_quarter(zip_path: Path, *, raw_document_id: int | None = None) -> dict[str, int]:
    """Load one quarter's zip. Idempotent. Returns counts for the ingestion run."""
    skip = await _xml_loaded_accessions()
    stats = {"filings": 0, "skipped_xml": 0, "rows_new": 0, "rows_updated": 0, "quarantined": 0,
             "nature_blanked": 0}
    pending = 0
    # session_scope commits at the end and rolls back on error; we also commit
    # every _COMMIT_EVERY filings so a long quarter is not one giant transaction.
    async with session_scope() as session:
        for accession, filed_on, raw_doc in iter_dataset_documents(zip_path):
            if accession in skip:
                stats["skipped_xml"] += 1
                continue
            doc = trusted_nature(raw_doc)
            if doc is not raw_doc:
                stats["nature_blanked"] += 1
            meta = FilingMeta(
                accession=accession, form_type=doc.document_type, filed_on=filed_on, accepted_at=None,
                url=filing_index_url(doc.issuer_cik, accession), primary_doc_url=None,
                detected_via="backfill", parser_version=PARSER_TAG, raw_document_id=None, live=False,
            )
            # A savepoint per filing: one bad filing is quarantined, the batch continues.
            try:
                async with session.begin_nested():
                    result = await load_ownership(session, meta, doc)
            except (ValueError, ParseError) as exc:
                stats["quarantined"] += 1
                session.add(Quarantine(source="edgar", raw_document_id=raw_document_id, filing_ref=accession,
                                       reason=f"data set load failed: {exc}"[:2000]))
                continue
            stats["filings"] += 1
            stats["rows_new"] += result.new_rows
            stats["rows_updated"] += result.updated_rows
            pending += 1
            if pending >= _COMMIT_EVERY:
                await session.commit()
                pending = 0
                log.info("backfill.progress", zip=zip_path.name, **stats)
    return stats


async def quarters_already_loaded(labels: list[str]) -> set[str]:
    """Quarters with a successful run by this reader version (the SEC doesn't revise posted data sets)."""
    if not labels:
        return set()
    async with session_scope() as session:
        rows = await session.execute(text(
            "SELECT DISTINCT stats->>'quarter' FROM ingestion_runs "
            "WHERE job = 'edgar.backfill_insider_dataset' AND status = 'ok' "
            "AND stats->>'quarter' = ANY(:labels) AND stats->>'reader_version' = :version"),
            {"labels": labels, "version": READER_VERSION})
        return {str(r[0]) for r in rows}


async def backfill_insider_datasets(quarters: list[str] | None = None, since: str | None = None,
                                    force: bool = False) -> None:
    """Entry point for `smt backfill`: download and load each requested quarter, newest first.

    Quarters already loaded by the same reader version are skipped (the command
    is resumable); `force` loads them again, which is harmless because loading
    is idempotent.
    """
    wanted = quarters_to_load(quarters, since)
    done = set() if force else await quarters_already_loaded([f"{y}q{q}" for y, q in wanted])
    if done:
        print(f"Already loaded, skipping: {', '.join(sorted(done))} (use --force to load again).")
    store = RawStore()
    client = shared_client()
    log.info("backfill.start", quarters=[f"{y}q{q}" for y, q in wanted], skipped=sorted(done))
    for year, quarter in wanted:
        label = f"{year}q{quarter}"
        if label in done:
            continue
        async with session_scope() as session:
            run = IngestionRun(job="edgar.backfill_insider_dataset", source="edgar", status="running",
                               stats={"quarter": label, "reader_version": READER_VERSION})
            session.add(run)
            await session.flush()
            run_id = run.id
        status, error, stats = "ok", None, {}
        try:
            path, raw_id = await _download(client, store, year, quarter)
            stats = await load_quarter(path, raw_document_id=raw_id)
            print(f"{label}: {stats['filings']:,} filings loaded, {stats['skipped_xml']:,} already had XML, "
                  f"{stats['quarantined']:,} quarantined.")
        except (FileNotFoundError, ParseError) as exc:
            status, error = "failed", str(exc)
            print(f"{label}: failed: {exc}")
        async with session_scope() as session:
            await session.execute(update(IngestionRun).where(IngestionRun.id == run_id).values(
                status=status, error=error, finished_at=text("now()"),
                docs_fetched=stats.get("filings", 0), rows_new=stats.get("rows_new", 0),
                rows_updated=stats.get("rows_updated", 0), parse_failures=stats.get("quarantined", 0),
                stats={"quarter": label, "reader_version": READER_VERSION, **stats},
            ))
    log.info("backfill.done")
