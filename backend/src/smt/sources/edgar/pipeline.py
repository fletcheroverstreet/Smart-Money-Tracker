"""Fetch, store, parse and load one EDGAR filing, driven by a durable queue.

Steps for one accession (spec §2.1, raw first):
1. GET the filing's `index.json`, the only reliable list of its documents
   (filenames vary: ownership.xml, form4.xml, wk-form4_<ts>.xml...). Never guess.
2. GET the candidate .xml documents and pick the one whose ROOT ELEMENT
   matches the form (`ownershipDocument` or a Form 144 `edgarSubmission`).
3. Store the raw bytes (compressed, by hash) and record them in `raw_documents`.
4. Parse. A parse failure quarantines the document and the queue item fails
   loudly; it is never silently dropped.
5. Load everything plus feed events, and NOTIFY, in ONE transaction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import psycopg.errors
from lxml import etree
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from smt.db.models import EdgarQueue, Filing, Quarantine, RawDocument
from smt.db.session import session_scope
from smt.domain.business_days import filing_date_for
from smt.logs import get_logger
from smt.net.client import Fetched, FetchError, HttpClient, SecThrottled
from smt.net.ratelimit import Priority
from smt.net.rawstore import RawStore
from smt.sources.forms import form144 as form144_parser
from smt.sources.forms import ownership as ownership_parser
from smt.sources.forms.errors import ParseError

log = get_logger("edgar.pipeline")
ET = ZoneInfo("America/New_York")

OWNERSHIP_FORMS = frozenset({"3", "3/A", "4", "4/A", "5", "5/A"})
FORM144_FORMS = frozenset({"144", "144/A"})
SUPPORTED_FORMS = OWNERSHIP_FORMS | FORM144_FORMS
MAX_ATTEMPTS = 6
#: SEC throttling or outages don't count as attempts; such items are retried this often...
TRANSIENT_RETRY = timedelta(minutes=5)
#: ...until they have waited this long since they were found.
TRANSIENT_GIVE_UP = timedelta(days=3)
#: A filing accepted longer ago than this is history, not news: it loads without
#: appearing on the Live feed (e.g. the first poll pages back weeks into the Form 3 feed).
LIVE_WINDOW = timedelta(hours=6)


def archive_dir(cik: str, accession: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}"


def index_page_url(cik: str, accession: str) -> str:
    return f"{archive_dir(cik, accession)}/{accession}-index.htm"


@dataclass(frozen=True)
class QueueItem:
    accession: str
    form_type: str
    cik: str
    accepted_at: datetime | None
    filed_on: Any
    discovered_via: str
    attempts: int
    #: {cik: EDGAR conformed name}; empty for items queued before migration 0004.
    filer_names: dict[str, str] = field(default_factory=dict)
    discovered_at: datetime | None = None


async def enqueue(session: AsyncSession, *, accession: str, form_type: str, cik: str,
                  accepted_at: datetime | None, filed_on: Any, discovered_via: str,
                  priority: int = 0, filer_names: dict[str, str] | None = None,
                  revive_failed: bool = False) -> bool:
    """Add a filing to the queue unless it's already queued or loaded. Returns True if added
    (or, with `revive_failed`, if a failed item for it was put back in line)."""
    exists = (await session.execute(
        select(Filing.id).where(Filing.source == "edgar", Filing.source_key == accession)
    )).first()
    if exists:
        return False
    stmt = insert(EdgarQueue).values(
        accession=accession, form_type=form_type, cik=cik, accepted_at=accepted_at, filed_on=filed_on,
        discovered_via=discovered_via, priority=priority, filer_names=filer_names or {},
    )
    if revive_failed:
        # The nightly reconciliation found the filing again: a failure (an SEC outage, say)
        # gets a fresh set of attempts instead of being lost. It counts as rediscovered, so the
        # TRANSIENT_GIVE_UP clock (measured from discovered_at) starts again too.
        stmt = stmt.on_conflict_do_update(
            index_elements=["accession"],
            set_={"status": "pending", "attempts": 0, "next_attempt_at": text("now()"), "last_error": None,
                  "discovered_at": text("now()")},
            where=EdgarQueue.status == "failed",
        )
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=["accession"])
    return (await session.execute(stmt.returning(EdgarQueue.accession))).first() is not None


async def claim_batch(limit: int, accessions: list[str] | None = None) -> list[QueueItem]:
    """Claim up to `limit` due items (highest priority, oldest first), or only the given
    `accessions`. Claimed items are pushed back 10 minutes so a crashed worker's items are
    retried automatically."""
    only = "AND accession = ANY(:accessions) " if accessions is not None else ""
    async with session_scope() as session:
        rows = (await session.execute(text(
            "UPDATE edgar_queue SET next_attempt_at = now() + interval '10 minutes', attempts = attempts + 1 "
            "WHERE accession IN (SELECT accession FROM edgar_queue WHERE status = 'pending' "
            f"AND next_attempt_at <= now() {only}ORDER BY priority, discovered_at LIMIT :limit "
            "FOR UPDATE SKIP LOCKED) "
            "RETURNING accession, form_type, cik, accepted_at, filed_on, discovered_via, attempts, filer_names, "
            "discovered_at"),
            {"limit": limit, "accessions": accessions})).all()
    return [QueueItem(accession=r.accession, form_type=r.form_type, cik=r.cik, accepted_at=r.accepted_at,
                      filed_on=r.filed_on, discovered_via=r.discovered_via, attempts=r.attempts,
                      filer_names=dict(r.filer_names or {}), discovered_at=r.discovered_at) for r in rows]


async def _finish(item: QueueItem, status: str, error: str | None = None, *, refund: bool = False) -> None:
    """Record an item's outcome. `refund` gives back the attempt the claim used (transient errors)."""
    async with session_scope() as session:
        values: dict[str, Any] = {"status": status, "last_error": error}
        if refund:
            values["attempts"] = max(0, item.attempts - 1)
            values["next_attempt_at"] = datetime.now(UTC) + TRANSIENT_RETRY
            await session.execute(update(EdgarQueue).where(EdgarQueue.accession == item.accession).values(**values))
            return
        if status == "done":
            values["finished_at"] = text("now()")
        elif status == "pending":
            # Exponential retry: 1, 2, 4, 8... minutes.
            values["next_attempt_at"] = datetime.now(UTC) + timedelta(minutes=2 ** max(0, item.attempts - 1))
        await session.execute(update(EdgarQueue).where(EdgarQueue.accession == item.accession).values(**values))


def _root_name(xml: bytes) -> str | None:
    """Local name of an XML document's root element, or None if it isn't XML."""
    try:
        parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=False)
        root = etree.fromstring(xml.lstrip(b"\xef\xbb\xbf"), parser=parser)
    except etree.XMLSyntaxError:
        return None
    return etree.QName(root).localname


def _index_last_modified(index: dict[str, Any], name: str) -> datetime | None:
    """A document's 'last-modified' in index.json is its acceptance time, in naive ET."""
    for item in index.get("directory", {}).get("item", []):
        if item.get("name") == name and item.get("last-modified"):
            try:
                return datetime.strptime(item["last-modified"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=ET)
            except ValueError:
                return None
    return None


async def _store_raw(session: AsyncSession, store: RawStore, fetched: Fetched, role: str) -> int:
    sha, rel = store.put(fetched.content)
    stmt = insert(RawDocument).values(
        source="edgar", url=fetched.url, sha256=sha, http_status=fetched.status,
        etag=fetched.header("etag"), last_modified=fetched.header("last-modified"),
        content_type=fetched.header("content-type"), byte_size=len(fetched.content),
        storage_path=rel, doc_role=role,
    ).on_conflict_do_update(index_elements=["url", "sha256"], set_={"fetched_at": text("now()")}
                            ).returning(RawDocument.id)
    return int((await session.execute(stmt)).scalar_one())


async def process_item(client: HttpClient, store: RawStore, item: QueueItem, *,
                       priority: Priority = Priority.LIVE, live: bool = True) -> list[int]:
    """Fetch, store, parse and load one queued filing. Returns new feed event ids."""
    base = archive_dir(item.cik, item.accession)
    index_resp = await client.get(f"{base}/index.json", priority=priority)
    if index_resp.status == 404:
        if item.filed_on is not None and item.filed_on < datetime.now(UTC).date() - REMOVED_AFTER:
            # Days later and still no index: EDGAR sometimes removes a filing after accepting it
            # (it stays in that day's index). If the full submission is gone too, it's removed.
            full = await client.get(f"https://www.sec.gov/Archives/edgar/data/{int(item.cik)}/{item.accession}.txt",
                                    priority=priority)
            if full.status == 404:
                raise FilingRemoved(f"{item.accession}: EDGAR no longer has this filing")
        raise FileNotFoundError(f"{item.accession}: index.json not found (yet)")
    index = json.loads(index_resp.content)
    names = [i["name"] for i in index.get("directory", {}).get("item", [])
             if str(i.get("name", "")).lower().endswith(".xml")]
    wanted = "ownershipDocument" if item.form_type in OWNERSHIP_FORMS else "edgarSubmission"

    chosen: tuple[str, Fetched] | None = None
    for name in names:
        doc = await client.get(f"{base}/{name}", priority=priority)
        if doc.ok and _root_name(doc.content) == wanted:
            chosen = (name, doc)
            break
    if chosen is None:
        raise ParseError(f"{item.accession}: no {wanted} XML among {names}")
    name, doc = chosen

    accepted_at = item.accepted_at or _index_last_modified(index, name)
    filed_on = item.filed_on or (filing_date_for(accepted_at, item.form_type) if accepted_at else None)
    from smt.ingest.ownership import FilingMeta, load_ownership  # local: avoid import cycles

    # Parse BEFORE opening the load transaction, so a failure can be quarantined
    # and committed on its own (a rollback would otherwise erase the record).
    parsed_own = None
    parsed_144 = None
    try:
        if item.form_type in OWNERSHIP_FORMS:
            parsed_own = ownership_parser.parse_ownership(doc.content)
            version = ownership_parser.PARSER_VERSION
        else:
            parsed_144 = form144_parser.parse_form144(doc.content)
            version = form144_parser.PARSER_VERSION
    except ParseError as exc:
        async with session_scope() as session:
            await _store_raw(session, store, index_resp, "index")
            raw_id = await _store_raw(session, store, doc, "primary")
            await session.execute(update(RawDocument).where(RawDocument.id == raw_id).values(
                parse_status="quarantined", parse_error=str(exc)[:2000]))
            await session.execute(insert(Quarantine).values(
                source="edgar", raw_document_id=raw_id, filing_ref=item.accession,
                reason=f"parse error: {exc}"[:2000]))
        raise

    async with session_scope() as session:
        await _store_raw(session, store, index_resp, "index")
        raw_id = await _store_raw(session, store, doc, "primary")
        meta = FilingMeta(
            accession=item.accession, form_type=item.form_type, filed_on=filed_on, accepted_at=accepted_at,
            url=index_page_url(item.cik, item.accession), primary_doc_url=doc.url,
            detected_via=item.discovered_via, parser_version=version, raw_document_id=raw_id, live=live,
            filer_names=item.filer_names,
        )
        if parsed_own is not None:
            result = await load_ownership(session, meta, parsed_own)
            from smt.ingest.form144 import match_sales_for

            sellers = (await session.execute(text(
                "SELECT DISTINCT o.entity_id, t.issuer_entity_id FROM transactions t "
                "JOIN transaction_owners o ON o.transaction_id = t.id "
                "WHERE t.filing_id = :f AND t.code = 'S'"), {"f": result.filing_id})).all()
            for issuer_id in {r[1] for r in sellers if r[1]}:
                await match_sales_for(session, [r[0] for r in sellers if r[1] == issuer_id], issuer_id)
        else:
            from smt.ingest.form144 import load_form144

            assert parsed_144 is not None
            result = await load_form144(session, meta, parsed_144)
        if result.feed_event_ids:
            from smt.ingest.feed import notify_feed

            await notify_feed(session, result.feed_event_ids)
    if accepted_at:
        lag = (datetime.now(UTC) - accepted_at).total_seconds()
        log.info("filing.loaded", accession=item.accession, form=item.form_type, rows=result.new_rows,
                 lag_seconds=round(lag, 1), notes=result.notes or None)
    return result.feed_event_ids


class FilingRemoved(Exception):
    """EDGAR accepted the filing (it is in that day's index) but no longer has it."""


#: A filing whose index is still missing this long after its filing date may have been removed.
REMOVED_AFTER = timedelta(days=2)


class _DatabaseContention(Exception):
    """A deadlock or serialization failure: Postgres cancelled the load so another could finish."""


async def _load_or_contention(coro: Any) -> Any:
    try:
        return await coro
    except DBAPIError as exc:
        if isinstance(exc.orig, (psycopg.errors.DeadlockDetected, psycopg.errors.SerializationFailure)):
            raise _DatabaseContention(str(exc.orig)) from exc
        raise


async def process_queue(client: HttpClient, store: RawStore, *, limit: int = 50,
                        priority: Priority = Priority.LIVE,
                        accessions: list[str] | None = None) -> dict[str, int]:
    """Process due queue items (only `accessions`, when given). Returns counts for the run record."""
    counts = {"done": 0, "retry": 0, "failed": 0, "events": 0}
    for item in await claim_batch(limit, accessions):
        try:
            live = (item.discovered_via == "feed" and item.accepted_at is not None
                    and datetime.now(UTC) - item.accepted_at < LIVE_WINDOW)
            events = await _load_or_contention(process_item(client, store, item, priority=priority, live=live))
            await _finish(item, "done")
            counts["done"] += 1
            counts["events"] += len(events)
        except FilingRemoved as exc:
            # Not a failure of ours: nothing is left to fetch. Kept as "gone" so /status can say so.
            await _finish(item, "gone", str(exc)[:2000])
            counts["gone"] = counts.get("gone", 0) + 1
            log.info("filing.removed_by_edgar", accession=item.accession)
        except ParseError as exc:
            await _finish(item, "failed", str(exc)[:2000])
            counts["failed"] += 1
        except (SecThrottled, FetchError, httpx.TransportError, _DatabaseContention) as exc:
            # The SEC throttled us or was down, or the load collided with another job's update:
            # not this filing's fault, so it doesn't use up an attempt. It is retried every few
            # minutes for up to TRANSIENT_GIVE_UP.
            too_old = item.discovered_at is not None and datetime.now(UTC) - item.discovered_at > TRANSIENT_GIVE_UP
            await _finish(item, "failed" if too_old else "pending", f"{type(exc).__name__}: {exc}"[:2000],
                          refund=not too_old)
            counts["failed" if too_old else "retry"] += 1
            log.warning("filing.retry_transient", accession=item.accession, error=str(exc)[:300])
        except Exception as exc:
            final = item.attempts >= MAX_ATTEMPTS
            await _finish(item, "failed" if final else "pending", f"{type(exc).__name__}: {exc}"[:2000])
            counts["failed" if final else "retry"] += 1
            log.warning("filing.retry", accession=item.accession, attempt=item.attempts, error=str(exc)[:300])
    return counts
