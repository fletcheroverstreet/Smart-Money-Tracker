"""The Ctrl+K command palette: people, funds, companies and tickers in one search.

Tickers match by prefix (exact tickers first). Names match by trigram
similarity (pg_trgm), so "berkshre" still finds Berkshire.

The order is: tickers, then up to three companies whose name starts with the
query as a whole word, then people and funds, then the other companies. Enter
opens the first hit, so "apple" opens Apple Inc. and not a person named Apple.
A company is one row, pointing at its common stock.

A person or fund is labelled with a role and company ("Director, Snowflake Inc."),
so two people with the same name can be told apart. It is the role the People
directory and the profile summary introduce them by (LEAD_ROLE_ORDER in
smt.api.queries): an officer role first, the most senior title first, then a
board seat, then 10% ownership. Tim Cook reads "Chief Executive Officer, Apple
Inc." even though his latest filing is as a NIKE director.
"""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Query, Response
from sqlalchemy import text

from smt.api.deps import Session
from smt.api.queries import LEAD_ROLE_ORDER, entity_link, org_tickers
from smt.api.schemas import SearchHit, SearchResults
from smt.api.wording import company_name, role_line

router = APIRouter(prefix="/api/search", tags=["search"])

#: What a person or fund with no reported role is labelled. A person gets no label:
#: the palette's kind column already says "Person".
_NO_ROLE = {"fund": "Fund or manager", "bank": "Bank"}

#: How many companies whose name starts with the query are listed before the people.
_LEADING_COMPANIES = 3


@router.get("")
async def search(session: Session, response: Response,
                 q: Annotated[str, Query(min_length=1, max_length=80)]) -> SearchResults:
    query = q.strip()
    hits: list[SearchHit] = []
    tickers = (await session.execute(text("""
        SELECT DISTINCT ON (x.value) x.value AS ticker, s.name
        FROM security_ids x JOIN securities s ON s.id = x.security_id
        WHERE x.scheme = 'ticker' AND x.valid_to IS NULL AND x.value LIKE :prefix
        ORDER BY x.value, (x.value = :exact) DESC
        LIMIT 6"""), {"prefix": query.upper().replace("%", "") + "%", "exact": query.upper()})).all()
    tickers = sorted(tickers, key=lambda r: (r.ticker != query.upper(), len(r.ticker)))
    hits += [SearchHit(kind="ticker", label=r.ticker, sublabel=company_name(r.name, [r.ticker]),
                      href=f"/tickers/{r.ticker}")
             for r in tickers]
    # The 8 best name matches first, then each one's role: the role lookup runs 8 times on
    # the roles index (entity_id leads it), never across every match, so typing stays fast.
    # The company's tickers keep an acronym such as "ACNB" in capitals (company_name).
    people = (await session.execute(text(f"""
        SELECT p.id, p.name, p.slug, p.kind, r.role_type, r.title, org.name AS org_name,
               {org_tickers("org")} AS org_tickers
        FROM (SELECT id, name, slug, kind, (name ILIKE :prefix) AS is_prefix,
                     (lower(name) ~ :token) AS is_token, similarity(name, :q) AS score
              FROM entities
              WHERE (name % :q OR name ILIKE :like)
                -- a company is found under its ticker (below), except a company with no ticker
                -- that files as an insider (Form 3/4/5) or as a Form 144 seller: its /people page
                -- is its only page. Thrivent or 'Sport City Cadiz S.L.' file Form 4s; Metropolitan
                -- Life Insurance Co (entity 62148) files only Form 144s and is tagged
                -- form144_filer and name_from_sec_record, with no 'insider' tag, like 82 other
                -- company-kind 144 filers (2026-09-29). A ticker-less issuer that never filed
                -- either way (tagged issuer only) has no /people page worth finding.
                AND (kind <> 'company' OR (tags && ARRAY['insider', 'form144_filer'] AND NOT EXISTS (
                     SELECT 1 FROM securities s WHERE s.issuer_entity_id = entities.id
                     AND s.primary_ticker IS NOT NULL)))
              -- a whole-word match ("feiger" in Mitchell Stuart Feiger) outranks a near one (Feigin)
              ORDER BY is_prefix DESC, is_token DESC, score DESC, id LIMIT 8) p
        -- the role the directory shows for them (LEAD_ROLE_ORDER)
        LEFT JOIN LATERAL (SELECT r.role_type, r.title, r.org_entity_id FROM roles r WHERE r.entity_id = p.id
                           ORDER BY {LEAD_ROLE_ORDER} LIMIT 1) r ON true
        LEFT JOIN entities org ON org.id = r.org_entity_id
        ORDER BY p.is_prefix DESC, p.is_token DESC, p.score DESC, p.id"""),
        {"q": query, "like": f"%{query}%", "prefix": f"{query}%",
         "token": rf"\m{re.escape(query.lower())}\M"})).all()
    # An organisation's name is stored as filed ("SPORT CITY CADIZ S.L.", "CANTOR FITZGERALD,
    # L. P."): a company, fund, bank or index manager is shown as its profile and the People
    # directory show it (entity_link: company_name for every kind but a person).
    people_hits = [
        SearchHit(kind=r.kind, label=entity_link(r.name, r.slug, r.kind).name,
                  sublabel=role_line(r.title, r.role_type, r.org_name, r.org_tickers or ()) or _NO_ROLE.get(r.kind),
                  href=f"/people/{r.slug}") for r in people]
    # One row per company, pointing at its common stock: Goldman Sachs Group has GS, GSCE and
    # the preferred classes GS-PA, GS-PC and GS-PD, and a new listing has units and warrants
    # (ISNR, ISNRU, ISNRW). The ticker without a class suffix, then the shortest, is the
    # common stock; the other classes are found by typing their ticker.
    companies = (await session.execute(text("""
        SELECT e.name, best.ticker, (lower(e.name) ~ :leads) AS leads
        FROM entities e
        JOIN LATERAL (SELECT s.primary_ticker AS ticker FROM securities s
                      WHERE s.issuer_entity_id = e.id AND s.primary_ticker IS NOT NULL
                      ORDER BY (s.primary_ticker ~ '[-.]'), length(s.primary_ticker), s.primary_ticker
                      LIMIT 1) best ON true
        WHERE e.kind = 'company' AND (e.name % :q OR e.name ILIKE :like)
        ORDER BY leads DESC, similarity(e.name, :q) DESC, e.id LIMIT 6"""),
        {"q": query, "like": f"%{query}%", "leads": rf"^{re.escape(query.lower())}\M"})).all()
    company_hits = [(r.leads, SearchHit(kind="company", label=company_name(r.name, [r.ticker]), sublabel=r.ticker,
                                        href=f"/tickers/{r.ticker}")) for r in companies]
    # Enter opens the first hit, so a company whose name starts with what was typed ("apple":
    # Apple Inc.) comes before people who share the word (Sarah E. Apple). At most
    # _LEADING_COMPANIES of them, so the people stay in view; the other company matches
    # ("Pineapple Financial") follow the people.
    leading = [hit for leads, hit in company_hits if leads][:_LEADING_COMPANIES]
    ordered = hits + leading + people_hits + [hit for _, hit in company_hits if hit not in leading]
    unique: dict[str, SearchHit] = {}
    for hit in ordered:  # a company already listed under its ticker isn't listed twice
        unique.setdefault(hit.href, hit)
    response.headers["Cache-Control"] = "public, max-age=60"
    return SearchResults(query=query, hits=list(unique.values())[:16])
