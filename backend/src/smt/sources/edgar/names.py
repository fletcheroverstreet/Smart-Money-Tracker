"""Fix people whose names came from Form 144 free text (job `edgar.verify_names`).

A Form 144 names the seller in free text, and filing agents disagree on word
order ("Timothy Cutt" vs "Dageville Benoit"). The loader now names sellers
from EDGAR's conformed name when the feed or daily index supplied it; anyone
named from free text is tagged `name_unverified` (see smt.ingest.entities).

This job clears the tag with a trustworthy name, cheapest source first:

1. The person's own Form 3/4/5: the reporting-owner name there is EDGAR's
   conformed "LAST FIRST MIDDLE" form (kept in filing_parties.relationship).
2. Otherwise their SEC submissions record (data.sec.gov), whose `name` is the
   conformed name. At most `SUBMISSIONS_PER_RUN` requests per run, on the
   REFERENCE lane of the shared SEC rate limiter, stored raw-first.
"""

from __future__ import annotations

import json
from compression import zstd
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from smt.db.models import Entity
from smt.db.session import session_scope
from smt.domain.names import NameFrequencies, looks_like_organization
from smt.ingest.entities import NAME_UNVERIFIED, EntityRef, apply_identity, rename_from_conformed
from smt.net.client import FetchError, SecThrottled, shared_client
from smt.net.ratelimit import Priority
from smt.net.rawstore import RawStore
from smt.sources.edgar.pipeline import _store_raw
from smt.worker.scheduler import JobContext

log = structlog.get_logger(__name__)

SUBMISSIONS_PER_RUN = 50

_UNVERIFIED_SQL = """
    SELECT e.id, e.kind, e.name, e.slug, x.value AS cik,
           (SELECT fp.relationship->>'raw_name'
              FROM filing_parties fp JOIN filings f ON f.id = fp.filing_id
             WHERE fp.entity_id = e.id AND fp.role = 'reporting_owner'
               AND f.form_type IN ('3', '3/A', '4', '4/A', '5', '5/A')
               AND fp.relationship->>'raw_name' IS NOT NULL
             ORDER BY f.filed_on DESC NULLS LAST LIMIT 1) AS raw_name
      FROM entities e JOIN entity_ids x ON x.entity_id = e.id AND x.scheme = 'sec_cik'
     WHERE :tag = ANY(e.tags)
     ORDER BY e.id
     LIMIT :limit
"""


def submissions_url(cik: str) -> str:
    return f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json"


def conformed_name_from_submissions(content: bytes) -> str | None:
    """The `name` field of a submissions JSON document, or None if absent or unreadable."""
    try:
        data: Any = json.loads(content)
    except ValueError:
        return None
    name = data.get("name") if isinstance(data, dict) else None
    return name.strip() if isinstance(name, str) and name.strip() else None


@dataclass(frozen=True)
class IdentityInputs:
    """What refresh_from_own_filings derives each filer's name and kind from.

    `names`: entity id -> (CIK, EDGAR's conformed name). The name is the one on the owner's latest
    Form 3/4/5 fetched from EDGAR (a quarterly data set row only when there is none), or, for a
    filer never seen on a Form 3/4/5 whose name came from the SEC submissions record (tag
    `name_from_sec_record`), the `name` in the stored submissions JSON.
    `signatures`: entity id -> each signer name on the owner's own filings with the number of
    filings that carry it. `frequencies`: word counts over every conformed name of a person.
    """

    names: dict[int, tuple[str, str]]
    signatures: dict[int, dict[str, int]]
    frequencies: NameFrequencies


async def identity_inputs(session: AsyncSession, store: RawStore | None = None) -> IdentityInputs:
    """Read the inputs of refresh_from_own_filings. Only SELECTs and reads of the raw store, so a
    read-only dry run over the real database uses exactly what the job uses."""
    rows = (await session.execute(text("""
        SELECT DISTINCT ON (fp.entity_id) fp.entity_id, x.value AS cik, fp.relationship->>'raw_name' AS raw_name
          FROM filing_parties fp
          JOIN filings f ON f.id = fp.filing_id
          JOIN entity_ids x ON x.entity_id = fp.entity_id AND x.scheme = 'sec_cik'
         WHERE fp.role = 'reporting_owner' AND f.form_type IN ('3', '3/A', '4', '4/A', '5', '5/A')
           AND fp.relationship->>'raw_name' IS NOT NULL
         ORDER BY fp.entity_id, (f.parser_version LIKE 'dataset/%'), f.filed_on DESC NULLS LAST, f.id DESC
    """))).all()
    names = {int(r.entity_id): (str(r.cik), str(r.raw_name)) for r in rows}
    # Named from the SEC record when first seen (a Form 144 seller with no Form 3/4/5, such as
    # Metropolitan Life Insurance Co): re-read the stored submissions JSON, so a better name rule
    # reaches these filers too. No request to the SEC.
    store = store or RawStore()
    for r in (await session.execute(text("""
        SELECT e.id, x.value AS cik, doc.storage_path
          FROM entities e
          JOIN entity_ids x ON x.entity_id = e.id AND x.scheme = 'sec_cik'
          JOIN LATERAL (SELECT d.storage_path FROM raw_documents d
                         WHERE d.doc_role = 'submissions'
                           AND d.url = 'https://data.sec.gov/submissions/CIK' || lpad(x.value, 10, '0') || '.json'
                           AND d.http_status = 200
                         ORDER BY d.fetched_at DESC LIMIT 1) doc ON true
         WHERE 'name_from_sec_record' = ANY(e.tags)
    """))).all():
        if int(r.id) in names:
            continue  # the owner's own Form 3/4/5 comes first
        try:
            name = conformed_name_from_submissions(store.get(r.storage_path))
        except (OSError, zstd.ZstdError) as exc:
            # A missing or corrupt raw file leaves this filer's stored name as it is; it must not
            # stop the refresh of every other filer (edgar.verify_names runs every 30 minutes).
            log.warning("names.submissions_unreadable", entity_id=r.id, error=str(exc))
            continue
        if name:
            names[int(r.id)] = (str(r.cik), name)
    # Every signer name on each owner's own filings, with the number of filings that carry it
    # (an owner who ever signed personally fixes where a double surname ends; attorneys'
    # signatures simply don't match; when signatures disagree, the one on more filings wins).
    signatures: dict[int, dict[str, int]] = {}
    for r in (await session.execute(text("""
        SELECT fp.entity_id, s.name, count(DISTINCT f.id) AS filings
          FROM filing_parties fp
          JOIN filings f ON f.id = fp.filing_id
          CROSS JOIN LATERAL jsonb_array_elements_text(coalesce(f.flags->'signatures', '[]'::jsonb)) AS s(name)
         WHERE fp.role = 'reporting_owner'
         GROUP BY fp.entity_id, s.name
         ORDER BY fp.entity_id, filings DESC, s.name
    """))).all():
        signatures.setdefault(int(r.entity_id), {})[str(r.name)] = int(r.filings)
    frequencies = NameFrequencies.from_names(
        name for _cik, name in names.values() if not looks_like_organization(name))
    return IdentityInputs(names, signatures, frequencies)


async def refresh_from_own_filings(session: AsyncSession) -> int:
    """Re-derive every reporting owner's name and kind from EDGAR's conformed name (see
    IdentityInputs: the latest Form 3/4/5, or the stored SEC record).

    This catches names that were set from weaker sources (Form 144 free text,
    older name rules), people misfiled as companies and organizations misfiled
    as people. Returns entities changed.
    """
    inputs = await identity_inputs(session)
    changed = 0
    ids = list(inputs.names)
    entities = {e.id: e for e in (await session.execute(
        select(Entity).where(Entity.id.in_(ids)))).scalars()} if ids else {}
    for entity_id, (cik, name) in inputs.names.items():
        entity = entities.get(entity_id)
        if entity is not None and await apply_identity(session, entity, cik, name,
                                                      inputs.signatures.get(entity_id, {}),
                                                      inputs.frequencies):
            changed += 1
    # A Form 144 seller never seen on a Form 3/4/5 was named from the notice: look it up.
    await session.execute(text("""
        UPDATE entities e SET tags = array_append(e.tags, :tag)
         WHERE 'form144_filer' = ANY(e.tags) AND NOT (:tag = ANY(e.tags))
           AND NOT EXISTS (SELECT 1 FROM filing_parties fp JOIN filings f ON f.id = fp.filing_id
                            WHERE fp.entity_id = e.id AND fp.role = 'reporting_owner'
                              AND f.form_type IN ('3', '3/A', '4', '4/A', '5', '5/A'))
           AND NOT ('name_from_sec_record' = ANY(e.tags))
    """), {"tag": NAME_UNVERIFIED})
    return changed


async def backfill_signatures(store: RawStore | None = None, cache_dir: Path | None = None) -> dict[str, int]:
    """Store the signer names of filings loaded before signatures were kept (filings.flags.signatures).

    From the stored XML for filings fetched from EDGAR, and from the
    OWNER_SIGNATURE table of the cached quarterly data sets for the rest. No
    requests to the SEC. Idempotent: only filings without the key are touched.
    Follow with refresh_from_own_filings to re-derive names. Returns counts.
    """
    import csv
    import io
    import zipfile

    from smt.config import get_settings
    from smt.sources.forms.ownership import parse_ownership

    store = store or RawStore()
    counts = {"from_xml": 0, "from_datasets": 0, "unreadable": 0}

    async def save(batch: dict[str, list[str]], key: str) -> None:
        if not batch:
            return
        async with session_scope() as session:
            result = await session.execute(text("""
                UPDATE filings f SET flags = f.flags || jsonb_build_object('signatures', v.names::jsonb)
                  FROM unnest(CAST(:accs AS text[]), CAST(:names AS text[])) AS v(accession, names)
                 WHERE f.source = 'edgar' AND f.source_key = v.accession AND NOT (f.flags ? 'signatures')
            """), {"accs": list(batch), "names": [json.dumps(v) for v in batch.values()]})
        counts[key] += int(getattr(result, "rowcount", 0) or 0)

    # 1. Filings fetched as XML: parse the stored document again.
    async with session_scope() as session:
        rows = (await session.execute(text("""
            SELECT DISTINCT ON (f.id) f.source_key, r.storage_path
              FROM filings f JOIN raw_documents r ON r.filing_id = f.id
             WHERE f.source = 'edgar' AND f.form_type IN ('3', '3/A', '4', '4/A', '5', '5/A')
               AND NOT (f.flags ? 'signatures')
             ORDER BY f.id, r.fetched_at DESC
        """))).all()
    batch: dict[str, list[str]] = {}
    for r in rows:
        try:
            doc = parse_ownership(store.get(r.storage_path))
        except Exception:  # a document that no longer parses keeps no signatures; it is counted
            counts["unreadable"] += 1
            continue
        batch[r.source_key] = [name for name, _signed in doc.signatures]
        if len(batch) >= 2000:
            await save(batch, "from_xml")
            batch = {}
    await save(batch, "from_xml")

    # 2. Filings loaded from the quarterly data sets: their OWNER_SIGNATURE table.
    folder = cache_dir or get_settings().raw_store_dir.parent / "cache"
    for path in sorted(folder.glob("*_form345.zip")):
        names: dict[str, list[str]] = {}
        with zipfile.ZipFile(path) as archive:
            member = next((i for i in archive.infolist() if i.filename.upper() == "OWNER_SIGNATURE.TSV"), None)
            if member is None:
                continue
            with archive.open(member) as handle:
                reader = csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8", errors="replace"),
                                        delimiter="\t", quoting=csv.QUOTE_NONE)
                for rec in reader:
                    accession, name = (rec.get("ACCESSION_NUMBER") or "").strip(), (rec.get("OWNERSIGNATURENAME") or "").strip()
                    if accession and name:
                        names.setdefault(accession, []).append(name)
        items = list(names.items())
        for start in range(0, len(items), 5000):
            await save(dict(items[start:start + 5000]), "from_datasets")
    log.info("names.signatures_backfilled", **counts)
    return counts


async def verify_names(ctx: JobContext) -> None:
    """Scheduler entry point."""
    async with session_scope() as session:
        ctx.stats["refreshed_from_filings"] = await refresh_from_own_filings(session)
    async with session_scope() as session:
        rows = (await session.execute(text(_UNVERIFIED_SQL), {"tag": NAME_UNVERIFIED, "limit": 2000})).all()
    ctx.stats["unverified"] = len(rows)
    if not rows:
        return

    # 1. From their own ownership filings (no network).
    remaining = []
    async with session_scope() as session:
        for r in rows:
            if r.raw_name:
                await rename_from_conformed(session, EntityRef(r.id, r.kind, r.name, r.slug), r.cik, r.raw_name)
                ctx.rows_updated += 1
            else:
                remaining.append(r)
    ctx.stats["from_filings"] = ctx.rows_updated

    # 2. From the SEC submissions record, a bounded batch per run.
    client = shared_client()
    store = RawStore()
    fixed_online = 0
    for r in remaining[:SUBMISSIONS_PER_RUN]:
        try:
            fetched = await client.get(submissions_url(r.cik), priority=Priority.REFERENCE)
        except SecThrottled:
            raise
        except FetchError as exc:
            log.warning("names.fetch_failed", cik=r.cik, error=str(exc))
            continue
        ctx.docs_fetched += 1
        if not fetched.ok:
            continue
        name = conformed_name_from_submissions(fetched.content)
        async with session_scope() as session:
            await _store_raw(session, store, fetched, "submissions")
            if name:
                await rename_from_conformed(session, EntityRef(r.id, r.kind, r.name, r.slug), r.cik, name)
                # Remember the source, so the next run doesn't look the same person up again.
                await session.execute(text(
                    "UPDATE entities SET tags = array_append(tags, 'name_from_sec_record') "
                    "WHERE id = :id AND NOT ('name_from_sec_record' = ANY(tags))"), {"id": r.id})
                fixed_online += 1
    ctx.rows_updated += fixed_online
    ctx.stats["from_submissions"] = fixed_online
    ctx.stats["left_for_next_run"] = max(0, len(remaining) - SUBMISSIONS_PER_RUN)
