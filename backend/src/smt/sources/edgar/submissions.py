"""Company profiles from the SEC's submissions JSON, and sectors for every security.

`https://data.sec.gov/submissions/CIK##########.json` is the SEC's summary of
one filer: current name, **former names** with dates, the **SIC** industry
code and its description, and current **tickers with their exchanges**.

What this module does, once a day (job `edgar.refresh_submissions`):

1. Pick a bounded batch of issuers with recent insider activity whose profile
   is missing or older than REFRESH_DAYS, most recently active first.
2. Fetch each file on the REFERENCE lane of the shared SEC rate limiter (live
   polling always goes first), store it raw, then parse it into
   `issuer_profiles`. A malformed file is quarantined, never dropped.
3. Relabel every security's sector with `smt.domain.sectors.sector_for`: the
   curated map (lodestar's industry_map.csv) first, then the SIC code, else
   "unmapped". Each label records which of the three it came from.

Conditional GET: the submissions API sends no ETag or Last-Modified (checked
2026-09-24), so an unchanged file is detected by its SHA-256 instead. The
helper `conditional_headers` still sends validators whenever a stored copy
has them (the ticker file does).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from smt.config import get_settings
from smt.db.models import IssuerProfile, Quarantine, RawDocument, Security
from smt.db.session import session_scope
from smt.domain.names import pad_cik
from smt.domain.sectors import sector_for
from smt.logs import get_logger
from smt.net.client import Fetched, FetchError, HttpClient, SecThrottled, shared_client
from smt.net.ratelimit import Priority
from smt.net.rawstore import RawStore
from smt.sources.edgar.pipeline import SUPPORTED_FORMS
from smt.worker.scheduler import JobContext

log = get_logger("edgar.submissions")

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
PARSER_NAME = "sec_submissions"
PARSER_VERSION = "1"
#: An issuer with an insider filing (Forms 3/4/5/144) filed in this many days counts as active.
ACTIVE_DAYS = 180
#: An active issuer's profile is re-checked after this many days.
REFRESH_DAYS = 30


class SubmissionsError(ValueError):
    """The document is not a usable submissions file (it gets quarantined)."""


@dataclass(frozen=True)
class FormerName:
    """An earlier company name and the dates the SEC says it was used."""

    name: str
    valid_from: date | None
    valid_to: date | None

    def as_json(self) -> dict[str, str | None]:
        return {"name": self.name,
                "from": self.valid_from.isoformat() if self.valid_from else None,
                "to": self.valid_to.isoformat() if self.valid_to else None}


@dataclass(frozen=True)
class SubmissionsProfile:
    """The fields we keep from one submissions file."""

    cik: str
    name: str | None
    entity_type: str | None
    sic: str | None
    sic_description: str | None
    #: Current tickers and, at the same position, their exchange (None when the SEC gives none).
    tickers: tuple[str, ...]
    exchanges: tuple[str | None, ...]
    former_names: tuple[FormerName, ...]
    state_of_incorporation: str | None
    fiscal_year_end: str | None


# ---------------------------------------------------------------- parsing
def _text(value: Any) -> str | None:
    """A trimmed string, or None for missing/blank values."""
    if value is None:
        return None
    out = str(value).strip()
    return out or None


def _date(value: Any) -> date | None:
    """'2021-02-17T05:00:00.000Z' -> 2021-02-17. (05:00 UTC is midnight Eastern, the date the SEC means.)"""
    raw = _text(value)
    if raw is None:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def parse_submissions(payload: bytes) -> SubmissionsProfile:
    """Parse one submissions JSON. Raises SubmissionsError if it isn't one."""
    try:
        data = json.loads(payload)
    except ValueError as exc:
        raise SubmissionsError(f"not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or _text(data.get("cik")) is None:
        raise SubmissionsError("no 'cik' field: this is not a submissions file")
    try:
        cik = pad_cik(str(data["cik"]))
    except (TypeError, ValueError) as exc:
        raise SubmissionsError(f"unreadable CIK {data.get('cik')!r}") from exc

    # SIC is a 3-4 digit code; anything else (blank for many funds) means "no SIC".
    sic = _text(data.get("sic"))
    if sic is not None and not (sic.isdigit() and len(sic) <= 4):
        sic = None

    raw_tickers = data.get("tickers") or []
    raw_exchanges = data.get("exchanges") or []
    if not isinstance(raw_tickers, list) or not isinstance(raw_exchanges, list):
        raise SubmissionsError("'tickers' and 'exchanges' must be lists")
    tickers: list[str] = []
    exchanges: list[str | None] = []
    for i, ticker in enumerate(raw_tickers):
        symbol = _text(ticker)
        if symbol is None:
            continue
        tickers.append(symbol.upper())
        exchanges.append(_text(raw_exchanges[i]) if i < len(raw_exchanges) else None)

    former: list[FormerName] = []
    for item in data.get("formerNames") or []:
        if isinstance(item, dict) and _text(item.get("name")):
            former.append(FormerName(name=str(item["name"]).strip(), valid_from=_date(item.get("from")),
                                     valid_to=_date(item.get("to"))))

    fiscal_year_end = _text(data.get("fiscalYearEnd"))
    return SubmissionsProfile(
        cik=cik, name=_text(data.get("name")), entity_type=_text(data.get("entityType")),
        sic=sic, sic_description=_text(data.get("sicDescription")),
        tickers=tuple(tickers), exchanges=tuple(exchanges), former_names=tuple(former),
        state_of_incorporation=(_text(data.get("stateOfIncorporation")) or "")[:10] or None,
        fiscal_year_end=fiscal_year_end if fiscal_year_end and len(fiscal_year_end) == 4 else None,
    )


# ---------------------------------------------------------------- raw storage (shared with reference.py)
async def store_raw_document(session: AsyncSession, store: RawStore, fetched: Fetched, *, role: str,
                             parser_name: str | None = None) -> tuple[int, str]:
    """Store a fetched reference document raw (idempotent). Returns (raw_documents.id, sha256).

    Identical bytes from the same URL are one row; fetching them again only
    refreshes `fetched_at` and the HTTP validators.
    """
    sha, rel = store.put(fetched.content)
    stmt = insert(RawDocument).values(
        source="edgar", url=fetched.url, sha256=sha, http_status=fetched.status,
        etag=fetched.header("etag"), last_modified=fetched.header("last-modified"),
        content_type=fetched.header("content-type"), byte_size=len(fetched.content),
        storage_path=rel, doc_role=role, parser_name=parser_name,
    )
    upsert = stmt.on_conflict_do_update(
        index_elements=["url", "sha256"],
        set_={"fetched_at": text("now()"), "etag": stmt.excluded.etag,
              "last_modified": stmt.excluded.last_modified},
    ).returning(RawDocument.id)
    return int((await session.execute(upsert)).scalar_one()), sha


async def conditional_headers(session: AsyncSession, url: str) -> dict[str, str]:
    """If-None-Match / If-Modified-Since from the newest stored copy of `url`.

    Only when that copy was parsed successfully: if the last load failed, we
    want the full document again, not a "304 Not Modified".
    """
    row = (await session.execute(
        select(RawDocument.etag, RawDocument.last_modified, RawDocument.parse_status)
        .where(RawDocument.url == url).order_by(RawDocument.fetched_at.desc()).limit(1))).first()
    if row is None or row.parse_status != "parsed":
        return {}
    headers: dict[str, str] = {}
    if row.etag:
        headers["If-None-Match"] = row.etag
    if row.last_modified:
        headers["If-Modified-Since"] = row.last_modified
    return headers


async def mark_parsed(session: AsyncSession, raw_id: int, *, parser_name: str, parser_version: str) -> None:
    await session.execute(update(RawDocument).where(RawDocument.id == raw_id).values(
        parse_status="parsed", parser_name=parser_name, parser_version=parser_version, parse_error=None))


async def quarantine_raw(session: AsyncSession, raw_id: int, *, ref: str, reason: str,
                         parser_name: str, parser_version: str) -> None:
    """Mark a stored document as quarantined and record why (never silently dropped)."""
    await session.execute(update(RawDocument).where(RawDocument.id == raw_id).values(
        parse_status="quarantined", parser_name=parser_name, parser_version=parser_version,
        parse_error=reason[:2000]))
    session.add(Quarantine(source="edgar", raw_document_id=raw_id, filing_ref=ref, reason=reason[:2000]))


# ---------------------------------------------------------------- which issuers
async def issuers_due(session: AsyncSession, *, limit: int, now: datetime | None = None) -> list[tuple[int, str]]:
    """(entity_id, cik) of recently active issuers whose profile is missing or old.

    Order: never-profiled issuers first, then by most recent insider filing,
    so a new company on the Live feed gets its sector at the next run.
    """
    now = now or datetime.now(UTC)
    rows = (await session.execute(text("""
        WITH active AS (
            SELECT issuer_entity_id AS entity_id, max(filed_on) AS last_filed
            FROM filings
            WHERE source = 'edgar' AND issuer_entity_id IS NOT NULL AND filed_on >= :active_since
              AND form_type = ANY(:forms)
            GROUP BY issuer_entity_id
        ), ciks AS (
            SELECT DISTINCT ON (entity_id) entity_id, value AS cik
            FROM entity_ids WHERE scheme = 'sec_cik' ORDER BY entity_id, valid_to NULLS FIRST, id
        )
        SELECT a.entity_id, c.cik
        FROM active a
        JOIN ciks c ON c.entity_id = a.entity_id
        LEFT JOIN issuer_profiles p ON p.entity_id = a.entity_id
        WHERE p.entity_id IS NULL OR p.checked_at < :stale_before
        ORDER BY (p.entity_id IS NULL) DESC, a.last_filed DESC, a.entity_id
        LIMIT :limit
    """), {"active_since": now.date() - timedelta(days=ACTIVE_DAYS), "forms": sorted(SUPPORTED_FORMS),
           "stale_before": now - timedelta(days=REFRESH_DAYS), "limit": limit})).all()
    return [(int(r.entity_id), str(r.cik)) for r in rows]


# ---------------------------------------------------------------- fetch, store, parse, load
async def _upsert_profile(session: AsyncSession, entity_id: int, cik: str, values: dict[str, Any]) -> None:
    stmt = insert(IssuerProfile).values(entity_id=entity_id, cik=cik, checked_at=text("now()"), **values)
    await session.execute(stmt.on_conflict_do_update(
        index_elements=["entity_id"], set_={"cik": cik, "checked_at": text("now()"), **values}))


async def refresh_issuer(client: HttpClient, store: RawStore, entity_id: int, cik: str) -> str:
    """Fetch, store, parse and save one issuer's profile.

    Returns what happened: updated | unchanged | not_found | quarantined.
    """
    url = SUBMISSIONS_URL.format(cik=cik)
    async with session_scope() as session:
        headers = await conditional_headers(session, url)
    fetched = await client.get(url, priority=Priority.REFERENCE, headers=headers or None)

    if fetched.status == 404:
        # No submissions file for this CIK. Remember we asked, so it isn't re-asked daily.
        async with session_scope() as session:
            await _upsert_profile(session, entity_id, cik, {"http_status": 404})
        return "not_found"
    if fetched.not_modified:
        async with session_scope() as session:
            await _upsert_profile(session, entity_id, cik, {"http_status": 200})
        return "unchanged"
    if not fetched.ok:
        raise FetchError(f"GET {url} returned HTTP {fetched.status}")

    # Raw first: the file is stored (and committed) before anything parses it.
    async with session_scope() as session:
        raw_id, sha = await store_raw_document(session, store, fetched, role="submissions",
                                               parser_name=PARSER_NAME)
    async with session_scope() as session:
        existing = await session.get(IssuerProfile, entity_id)
        if (existing is not None and existing.sha256 == sha and existing.parser_version == PARSER_VERSION
                and existing.http_status == 200):
            existing.checked_at = datetime.now(UTC)
            await mark_parsed(session, raw_id, parser_name=PARSER_NAME, parser_version=PARSER_VERSION)
            return "unchanged"

    try:
        profile = parse_submissions(fetched.content)
        if profile.cik != cik:
            raise SubmissionsError(f"file is for CIK {profile.cik}, expected {cik}")
    except SubmissionsError as exc:
        async with session_scope() as session:
            await quarantine_raw(session, raw_id, ref=f"CIK{cik}", reason=f"submissions file: {exc}",
                                 parser_name=PARSER_NAME, parser_version=PARSER_VERSION)
            # Mark it checked so a broken file isn't re-quarantined every day.
            await _upsert_profile(session, entity_id, cik, {"http_status": fetched.status})
        log.warning("submissions.quarantined", cik=cik, error=str(exc)[:300])
        return "quarantined"

    async with session_scope() as session:
        await _upsert_profile(session, entity_id, cik, {
            "http_status": 200, "sec_name": profile.name, "entity_type": profile.entity_type,
            "sic": profile.sic, "sic_description": profile.sic_description,
            "tickers": list(profile.tickers), "exchanges": list(profile.exchanges),
            "former_names": [f.as_json() for f in profile.former_names],
            "state_of_incorporation": profile.state_of_incorporation,
            "fiscal_year_end": profile.fiscal_year_end, "raw_document_id": raw_id, "sha256": sha,
            "parser_version": PARSER_VERSION, "updated_at": text("now()"),
        })
        await mark_parsed(session, raw_id, parser_name=PARSER_NAME, parser_version=PARSER_VERSION)
    return "updated"


# ---------------------------------------------------------------- sectors
def _norm_ticker(ticker: str) -> str:
    return ticker.strip().upper().replace(".", "-")


async def relabel_securities(session: AsyncSession) -> int:
    """Recompute sic, sector, industry and sector_source for every security. Returns rows changed.

    The SIC comes from the issuer's profile when we have one (else whatever
    the security already had). `sector_for` applies the curated map first, so
    edits to industry_map.csv also reach existing securities here. The
    exchange is filled from the profile when the ticker file didn't give one.
    """
    rows = (await session.execute(text("""
        SELECT s.id, s.primary_ticker, s.sic, s.sector, s.industry, s.sector_source, s.exchange,
               p.sic AS profile_sic, p.tickers AS profile_tickers, p.exchanges AS profile_exchanges
        FROM securities s LEFT JOIN issuer_profiles p ON p.entity_id = s.issuer_entity_id
    """))).all()
    changes: list[dict[str, Any]] = []
    for r in rows:
        sic = r.profile_sic or r.sic
        label = sector_for(r.primary_ticker, sic)
        exchange = r.exchange
        if exchange is None and r.primary_ticker and r.profile_tickers:
            # Filings write "BRK.B", the SEC's lists write "BRK-B": compare them the same way.
            tickers = [_norm_ticker(t) for t in r.profile_tickers]
            exchanges = list(r.profile_exchanges or [])
            position = tickers.index(_norm_ticker(r.primary_ticker)) if _norm_ticker(r.primary_ticker) in tickers else -1
            if 0 <= position < len(exchanges):
                exchange = exchanges[position]
        new = {"sic": sic, "sector": label.sector, "industry": label.industry,
               "sector_source": label.source, "exchange": exchange}
        old = {"sic": r.sic, "sector": r.sector, "industry": r.industry,
               "sector_source": r.sector_source, "exchange": r.exchange}
        if new != old:
            changes.append({"id": r.id, **new})
    if changes:
        # ORM bulk UPDATE by primary key: one statement, many parameter sets.
        await session.execute(update(Security), changes)
    return len(changes)


async def refresh_submissions(client: HttpClient, store: RawStore, *, limit: int,
                              now: datetime | None = None) -> dict[str, int]:
    """One bounded batch: profiles for due issuers, then sectors for every security."""
    counts = {"due": 0, "updated": 0, "unchanged": 0, "not_found": 0, "quarantined": 0,
              "fetch_errors": 0, "securities_relabelled": 0}
    async with session_scope() as session:
        due = await issuers_due(session, limit=limit, now=now)
    counts["due"] = len(due)
    for entity_id, cik in due:
        try:
            outcome = await refresh_issuer(client, store, entity_id, cik)
        except SecThrottled:
            raise  # the SEC wants us to stop: end the run, the watchdog reports it
        except FetchError as exc:
            counts["fetch_errors"] += 1
            log.warning("submissions.fetch_failed", cik=cik, error=str(exc)[:300])
            continue
        counts[outcome] += 1
    async with session_scope() as session:
        counts["securities_relabelled"] = await relabel_securities(session)
    return counts


async def refresh_submissions_job(ctx: JobContext) -> None:
    """Scheduled daily: a batch of company profiles, then sector labels for all securities."""
    counts = await refresh_submissions(shared_client(), RawStore(),
                                       limit=get_settings().submissions_batch_size)
    ctx.docs_fetched += counts["updated"] + counts["unchanged"] + counts["not_found"] + counts["quarantined"]
    ctx.rows_new += counts["updated"]
    ctx.rows_updated += counts["securities_relabelled"]
    ctx.parse_failures += counts["quarantined"]
    ctx.stats.update(counts)

