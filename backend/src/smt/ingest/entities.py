"""Find-or-create entities and securities by their identifiers.

Everything is keyed by identifiers, never by names: an SEC CIK identifies a
filer or issuer; a ticker (valid over a date range) identifies a security.
Names are display attributes and may change; identifiers don't.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from smt.db.models import Entity, EntityId, Security, SecurityId
from smt.domain.business_days import ET
from smt.domain.names import (
    NameFrequencies,
    Signatures,
    looks_like_fund,
    looks_like_organization,
    org_display,
    pad_cik,
    person_display_and_sort,
    slugify,
)
from smt.domain.sectors import sector_for
from smt.logs import get_logger

log = get_logger("ingest.entities")


@dataclass(frozen=True)
class EntityRef:
    id: int
    kind: str
    name: str
    slug: str


async def entity_by_id(session: AsyncSession, scheme: str, value: str) -> EntityRef | None:
    """Look up an entity by an external identifier."""
    row = (await session.execute(
        select(Entity.id, Entity.kind, Entity.name, Entity.slug)
        .join(EntityId, EntityId.entity_id == Entity.id)
        .where(EntityId.scheme == scheme, EntityId.value == value)
    )).first()
    return EntityRef(*row) if row else None


#: What filers type to mean "no ticker", compared with brackets, punctuation and spaces removed
#: ("[NONE]", "[NONE", "(none)", "N/A", "[N/A]", "Not applicable"). "NA" is also Nano Labs' real
#: ticker, so a bare "NA" is only a placeholder when it doesn't belong to the filing's own company.
_PLACEHOLDER_WORDS = frozenset({"", "NA", "NONE", "NULL", "TBD", "0", "NOTAPPLICABLE", "NOTICKER", "NOSYMBOL"})


def is_placeholder_ticker(ticker: str | None) -> bool:
    """True when a filing's ticker text means "none" rather than naming a symbol."""
    return ticker is None or re.sub(r"[^A-Z0-9]", "", ticker.upper()) in _PLACEHOLDER_WORDS


#: A US exchange ticker: 1-5 letters, maybe a class or unit suffix (BRK.B, BF-B, RDS/A, KCAC-UN).
_TICKER_SHAPE = re.compile(r"[A-Z]{1,5}(?:[.\-/][A-Z]{1,2})?")


def looks_like_ticker(ticker: str | None) -> bool:
    """False for text a filer typed in the ticker box that can't be an exchange ticker: a CUSIP
    ("21874U888"), a CIK ("1314152"), a company name ("GRAYBAR", "IDAHOPWR") or other words
    ("INX TOKEN", "PCG-PREF"). Such text is kept on the row (raw_ticker) but makes no ticker page."""
    return ticker is not None and _TICKER_SHAPE.fullmatch(ticker.strip().upper()) is not None

#: Tag on a person whose name came from free text (a Form 144 seller name) rather
#: than EDGAR's conformed name. A later conformed name replaces it; see
#: `rename_from_conformed` and the edgar.verify_names job.
NAME_UNVERIFIED = "name_unverified"


async def ensure_filer(session: AsyncSession, cik: str, edgar_name: str, *,
                       kind_hint: str | None = None, tags: tuple[str, ...] = (),
                       source: str = "edgar", trusted_name: bool = True,
                       signatures: Signatures = ()) -> EntityRef:
    """Find or create the entity for an SEC filer (person or organization) by CIK.

    `trusted_name` says whether `edgar_name` is EDGAR's conformed name for the
    CIK ("LAST FIRST MIDDLE": ownership XML, the feed, the daily index). Free
    text, like a Form 144's seller name, is not: its word order depends on the
    filing agent. A person created from free text is tagged NAME_UNVERIFIED,
    and the first trusted name seen later replaces it. `signatures` are the
    filing's signer names (see person_display_and_sort).
    """
    cik = pad_cik(cik)
    found = await entity_by_id(session, "sec_cik", cik)
    if found:
        if trusted_name:
            found = await rename_from_conformed(session, found, cik, edgar_name)
        if tags:
            await _add_tags(session, found.id, tags)
        return found

    kind, name, sort_name = identity_from_name(edgar_name, kind_hint, signatures)
    slug = slugify(name, suffix=cik.lstrip("0"))
    # Free text can't be trusted for the name or for person-vs-organization either (a Form 144
    # may name a family trust as the account holder while the filer is the person).
    all_tags = list(tags) + ([] if trusted_name else [NAME_UNVERIFIED])

    stmt = insert(Entity).values(kind=kind, name=name, sort_name=sort_name, slug=slug,
                                 tags=all_tags).on_conflict_do_nothing(index_elements=["slug"])
    await session.execute(stmt)
    entity_id = (await session.execute(select(Entity.id).where(Entity.slug == slug))).scalar_one()
    await session.execute(
        insert(EntityId).values(entity_id=entity_id, scheme="sec_cik", value=cik, source=source)
        .on_conflict_do_nothing(index_elements=["scheme", "value"])
    )
    # If another transaction created the same CIK concurrently, use that one.
    winner = await entity_by_id(session, "sec_cik", cik)
    assert winner is not None
    return winner


def identity_from_name(edgar_name: str, kind_hint: str | None = None,
                       signatures: Signatures = (),
                       frequencies: NameFrequencies | None = None) -> tuple[str, str, str]:
    """(kind, display name, sort name) for an EDGAR filer name.

    People come as "LAST FIRST MIDDLE" and are shown "First Middle Last";
    organizations keep their legal name, tidied. `kind_hint` forces the kind
    (an issuer is always a company). `signatures` are the signer names on the
    filer's own filings, each with the number of filings that carry it: they
    place the surname, and a person who signs their own name in natural order
    stays a person even when the name contains a company word ("TRUST JANE").
    `frequencies` (word counts over all conformed names) let a signature show
    a name EDGAR already has first-name first (see person_display_and_sort).
    """
    if kind_hint:
        kind = kind_hint
    elif looks_like_organization(edgar_name, signatures):
        kind = "fund" if looks_like_fund(edgar_name) else "company"
    else:
        kind = "person"
    if kind == "person":
        name, sort_name = person_display_and_sort(edgar_name, signatures, frequencies)
    else:
        name = org_display(edgar_name)
        # Sorted with a capital first, like a person's: the database sorts byte by byte (C
        # collation), so "abrdn Inc." (15812) or "venBio Global Strategic Fund IV, L.P." (64940)
        # would follow every Z in the People directory. The display keeps the filer's own case.
        sort_name = name[:1].upper() + name[1:]
    return kind, name, sort_name


def derive_identity(current_kind: str, tags: list[str], cik: str, conformed_name: str,
                    signatures: Signatures = (), frequencies: NameFrequencies | None = None,
                    ) -> tuple[str, str, str, str]:
    """(kind, name, sort name, slug) an entity gets from EDGAR's conformed name (see apply_identity).

    A known issuer keeps its kind. Otherwise only person vs organization is corrected: company vs
    fund is a finer call, so an organization that stays an organization keeps its kind.
    """
    keep_kind = "issuer" in tags  # an issuer stays a company even if its name reads like a fund
    kind, name, sort_name = identity_from_name(conformed_name, current_kind if keep_kind else None,
                                               signatures, frequencies)
    if not keep_kind and current_kind in ("company", "fund", "bank", "index_manager") and kind != "person":
        kind = current_kind
    return kind, name, sort_name, slugify(name, suffix=pad_cik(cik).lstrip("0"))


async def apply_identity(session: AsyncSession, entity: Entity, cik: str, conformed_name: str,
                         signatures: Signatures = (), frequencies: NameFrequencies | None = None) -> bool:
    """Set an entity's name (and, unless it is a known issuer, its kind) from EDGAR's conformed
    name. The slug follows the name when it is free; old links keep working because the API
    resolves a slug by its CIK suffix, and every page links people and organizations alike under
    /people/<slug>, so a kind change moves no page. Clears NAME_UNVERIFIED. Returns True if
    anything changed.
    """
    tags = list(entity.tags or [])
    kind, name, sort_name, slug = derive_identity(entity.kind, tags, cik, conformed_name, signatures, frequencies)
    changed = (entity.name, entity.sort_name, entity.kind) != (name, sort_name, kind) or NAME_UNVERIFIED in tags
    if not changed:
        return False
    taken = (await session.execute(
        select(Entity.id).where(Entity.slug == slug, Entity.id != entity.id))).first()
    entity.name, entity.sort_name, entity.kind = name, sort_name, kind
    if not taken:
        entity.slug = slug
    entity.tags = [t for t in tags if t != NAME_UNVERIFIED]
    await session.flush()
    return True


async def rename_from_conformed(session: AsyncSession, ref: EntityRef, cik: str, conformed_name: str) -> EntityRef:
    """Replace an unverified name (and kind) with one derived from EDGAR's conformed name.

    Does nothing unless the entity carries NAME_UNVERIFIED (a conformed name
    seen later never overrides one already trusted).
    """
    entity = await session.get(Entity, ref.id)
    if entity is None or NAME_UNVERIFIED not in (entity.tags or []):
        return ref
    await apply_identity(session, entity, cik, conformed_name)
    return EntityRef(entity.id, entity.kind, entity.name, entity.slug)


async def _add_tags(session: AsyncSession, entity_id: int, tags: tuple[str, ...]) -> None:
    entity = await session.get(Entity, entity_id)
    if entity is None:
        return
    merged = sorted(set(entity.tags or []) | set(tags))
    if merged != sorted(entity.tags or []):
        entity.tags = merged


async def ensure_issuer_security(session: AsyncSession, issuer: EntityRef, ticker: str | None, *,
                                 as_of: date | None = None, sic: str | None = None) -> int | None:
    """The issuer's primary tradable security, found by ticker or created from the filing.

    Insider filings report one issuer and its ticker. Insider activity is
    issuer-level, so rows link to the issuer's primary security. The row's own
    `security_title` keeps the exact class ("Class B Common Stock").
    """
    taken_by_other = False
    if ticker:
        ticker = ticker.strip().upper()
        # A placeholder is no ticker at all, except a bare "NA", which may be the company's own.
        if (is_placeholder_ticker(ticker) and ticker != "NA") or not looks_like_ticker(ticker):
            ticker = None
    if ticker:
        found = (await session.execute(
            select(SecurityId.security_id, Security.issuer_entity_id)
            .join(Security, Security.id == SecurityId.security_id)
            .where(
                SecurityId.scheme == "ticker", SecurityId.value == ticker,
                (SecurityId.valid_to.is_(None)) | (SecurityId.valid_to >= (as_of or datetime.now(ET).date())),
            ).order_by(SecurityId.valid_from.desc().nulls_last())
        )).all()
        # A ticker only identifies this company's security if the security is this company's
        # (or nobody's yet). Filers type placeholders ("NA", Nano Labs' real ticker) or tickers
        # since given to another company; those must not link to someone else's page.
        mine = [r for r in found if r.issuer_entity_id in (None, issuer.id)]
        if mine:
            await _link_issuer(session, mine[0].security_id, issuer.id)
            return int(mine[0].security_id)
        taken_by_other = bool(found)

    existing = (await session.execute(
        select(Security.id).where(Security.issuer_entity_id == issuer.id).order_by(Security.id)
    )).scalars().first()
    if existing:
        return int(existing)
    if not ticker or is_placeholder_ticker(ticker) or not looks_like_ticker(ticker) or taken_by_other:
        return None  # the filing's ticker text is kept on the row (raw_ticker)

    label = sector_for(ticker, sic)
    security = Security(
        issuer_entity_id=issuer.id, name=issuer.name, primary_ticker=ticker,
        sector=label.sector, industry=label.industry, sector_source=label.source, sic=sic,
    )
    session.add(security)
    await session.flush()
    await session.execute(
        insert(SecurityId).values(security_id=security.id, scheme="ticker", value=ticker,
                                  source="edgar_filing")
        .on_conflict_do_nothing()
    )
    return security.id


async def repair_security_links(session: AsyncSession) -> int:
    """Re-point rows linked to another company's security (made before the issuer check).

    Each such row moves to its own company's security when there is one, else to none.
    Feed events follow, and their payload ticker becomes the linked security's ticker.
    Returns rows moved.
    """
    moved = await session.execute(text("""
        UPDATE transactions t
           SET security_id = (SELECT s2.id FROM securities s2 WHERE s2.issuer_entity_id = t.issuer_entity_id
                               ORDER BY s2.is_active DESC, s2.id LIMIT 1)
          FROM securities s
         WHERE s.id = t.security_id AND s.issuer_entity_id IS NOT NULL
           AND s.issuer_entity_id IS DISTINCT FROM t.issuer_entity_id
    """))
    await session.execute(text("""
        UPDATE feed_events e
           SET security_id = t.security_id,
               payload = jsonb_set(e.payload, '{ticker}', coalesce(to_jsonb(s.primary_ticker), 'null'::jsonb))
          FROM transactions t LEFT JOIN securities s ON s.id = t.security_id
         WHERE e.transaction_id = t.id
           AND (e.security_id IS DISTINCT FROM t.security_id
                OR e.payload->>'ticker' IS DISTINCT FROM s.primary_ticker)
    """))
    return int(getattr(moved, "rowcount", 0) or 0) + await _remove_placeholder_securities(session)


async def _remove_placeholder_securities(session: AsyncSession) -> int:
    """Delete securities made from a placeholder ticker ("[NONE]", "[N/A]") before the check was
    complete. Their rows move to the company's real security when it has one, else to none (the
    filing's ticker text stays on each row as raw_ticker). A security the SEC's own ticker list
    names is never touched. Returns rows moved."""
    candidates = (await session.execute(text("""
        SELECT s.id, s.primary_ticker, s.issuer_entity_id,
               NOT EXISTS (SELECT 1 FROM security_ids x WHERE x.security_id = s.id AND x.scheme = 'ticker'
                             AND x.source IS DISTINCT FROM 'edgar_filing') AS from_filing_only
          FROM securities s
    """))).all()
    # A placeholder is removed even when the SEC's own ticker list carries it ('NONE.' for EBR
    # Systems, whose real symbol is EBRCZ); only Nano Labs' bare 'NA' is a real ticker. The
    # shape check applies only to symbols filers typed (the SEC's list has class forms like 'BRK-B').
    doomed = [c for c in candidates
              if (is_placeholder_ticker(c.primary_ticker) and c.primary_ticker != "NA")
              or (c.from_filing_only and not looks_like_ticker(c.primary_ticker))]
    moved = 0
    for c in doomed:
        target = (await session.execute(text("""
            SELECT id FROM securities WHERE issuer_entity_id = :issuer AND id <> :id
             ORDER BY is_active DESC, id LIMIT 1"""), {"issuer": c.issuer_entity_id, "id": c.id})).scalar()
        for table in ("transactions", "planned_sales", "feed_events"):
            result = await session.execute(text(f"UPDATE {table} SET security_id = :to WHERE security_id = :id"),
                                           {"to": target, "id": c.id})
            if table != "feed_events":
                moved += int(getattr(result, "rowcount", 0) or 0)
        await session.execute(text("DELETE FROM securities WHERE id = :id"), {"id": c.id})
        log.info("security.placeholder_removed", security_id=c.id, ticker=c.primary_ticker, moved_to=target)
    if doomed:
        # Feed events show the linked security's ticker, or none.
        await session.execute(text("""
            UPDATE feed_events e
               SET payload = jsonb_set(e.payload, '{ticker}',
                                       coalesce((SELECT to_jsonb(s.primary_ticker) FROM securities s
                                                  WHERE s.id = e.security_id), 'null'::jsonb))
             WHERE e.payload->>'ticker' = ANY(:tickers)
        """), {"tickers": [c.primary_ticker for c in doomed]})
    return moved


async def _link_issuer(session: AsyncSession, security_id: int, issuer_id: int) -> None:
    security = await session.get(Security, security_id)
    if security is not None and security.issuer_entity_id is None:
        security.issuer_entity_id = issuer_id
