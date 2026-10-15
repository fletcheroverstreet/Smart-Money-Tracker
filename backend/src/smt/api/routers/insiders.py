"""People: the insider directory, profiles, trade history and positions over time.

Per-person figures (the directory columns and a profile's stats and trades)
count every trade that lists the person as a reporting owner, via
transaction_owners: a joint filing is each owner's trade, so it counts on each
owner's row. Figures that add up across people (the ticker index, the Live
page's today strip) count each trade once, under its attributed owner.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import text

from smt.api import cursor
from smt.api.deps import Session
from smt.api.queries import (
    LEAD_ROLE_ORDER,
    PERSON_COUNTED,
    PERSON_TRADE_SQL,
    VALUE_OK,
    entity_link,
    org_tickers,
    parse_codes,
    title_rank,
    trade_row,
)
from smt.api.schemas import (
    DirectoryPage,
    DirectoryRow,
    PersonProfile,
    PersonStats,
    PlannedSaleOut,
    PositionPoint,
    PositionSeries,
    RoleOut,
    TradePage,
)
from smt.api.wording import (
    ROLE_LABELS,
    clean_title,
    company_name,
    form144_relationships,
    plan_sentence,
    title_in_sentence,
)

router = APIRouter(prefix="/api/people", tags=["people"])


_CIK_SUFFIX = re.compile(r"-(\d{1,10})$")

#: A Form 144 notice (planned_sales p, its filing f) that hasn't been replaced by an
#: amendment: the rule the ticker page's list uses.
CURRENT_NOTICE = "f.superseded_by_id IS NULL"


async def _entity(session: Session, slug: str) -> Any:
    """Find an entity by slug. Slugs end in the SEC CIK ("timothy-cutt-1234567"), so
    when a person is renamed (a corrected name changes the slug) the old link
    still resolves through the CIK; the response carries the current slug."""
    columns = "e.id, e.name, e.slug, e.kind, e.tags, e.summary, e.summary_source"
    row = (await session.execute(text(f"SELECT {columns} FROM entities e WHERE e.slug = :slug"),
                                 {"slug": slug})).first()
    if row is None and (match := _CIK_SUFFIX.search(slug)):
        row = (await session.execute(text(
            f"SELECT {columns} FROM entities e JOIN entity_ids x ON x.entity_id = e.id "
            "WHERE x.scheme = 'sec_cik' AND x.value = :cik"), {"cik": match.group(1).zfill(10)})).first()
    if row is None:
        raise HTTPException(status_code=404, detail="No such person or organization")
    return row


@router.get("/insiders")
async def directory(
    session: Session,
    response: Response,
    q: Annotated[str | None, Query(max_length=80)] = None,
    role: Annotated[str | None, Query(pattern="^(officer|director|ten_pct_owner|other_insider)$")] = None,
    sort: Annotated[str, Query(pattern="^(recent|buys|sells|name)$")] = "recent",
    cursor_: Annotated[str | None, Query(alias="cursor")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> DirectoryPage:
    """Insiders with their most senior role (an officer role first, the top title first, then a
    board seat, then 10% ownership) and open-market activity counts."""
    where = ["'insider' = ANY(e.tags)"]
    params: dict[str, Any] = {"limit": limit + 1}
    if q:
        where.append("(e.name ILIKE :q OR e.sort_name ILIKE :q)")
        params["q"] = f"%{q}%"
    if role:
        where.append("EXISTS (SELECT 1 FROM roles r WHERE r.entity_id = e.id AND r.role_type = :role)")
        params["role"] = role
    order = {
        "recent": ("stats.last_traded_on DESC NULLS LAST, e.id DESC", "last_traded_on"),
        # People with no trades have no stats row (NULL): they sort last, not first.
        "buys": ("stats.buys DESC NULLS LAST, e.id DESC", "buys"),
        "sells": ("stats.sells DESC NULLS LAST, e.id DESC", "sells"),
        "name": ("e.sort_name ASC, e.id ASC", "sort_name"),
    }[sort]
    offset = cursor.decode_offset(cursor_)
    params["offset"] = offset
    # Counts come from entity_trade_stats (migration 0008), refreshed every few minutes.
    rows = (await session.execute(text(f"""
        SELECT e.id, e.name, e.slug, e.kind, e.sort_name,
               coalesce(stats.trades, 0) AS trades, coalesce(stats.buys, 0) AS buys,
               coalesce(stats.sells, 0) AS sells, stats.last_traded_on,
               r.role_type, r.title, org.name AS org_name, org.slug AS org_slug, org.kind AS org_kind,
               {org_tickers("org")} AS org_tickers
        FROM entities e
        LEFT JOIN entity_trade_stats stats ON stats.entity_id = e.id
        -- the role the person is introduced by: officer (the most senior title first), then
        -- director, then 10% owner; most recently seen first (LEAD_ROLE_ORDER)
        LEFT JOIN LATERAL (SELECT * FROM roles r WHERE r.entity_id = e.id
                           ORDER BY {LEAD_ROLE_ORDER} LIMIT 1) r ON true
        LEFT JOIN entities org ON org.id = r.org_entity_id
        WHERE {" AND ".join(where)}
        ORDER BY {order[0]}
        LIMIT :limit OFFSET :offset"""), params)).all()
    items = [DirectoryRow(
        entity=entity_link(r.name, r.slug, r.kind),
        role=clean_title(r.title, r.role_type) if r.role_type else None,
        organization=(entity_link(r.org_name, r.org_slug, r.org_kind, r.org_tickers or ())
                      if r.org_slug else None),
        trades=r.trades, open_market_buys=r.buys, open_market_sells=r.sells, last_traded_on=r.last_traded_on,
    ) for r in rows[:limit]]
    response.headers["Cache-Control"] = "public, max-age=30"
    return DirectoryPage(items=items, next_cursor=cursor.encode(offset + limit) if len(rows) > limit else None)


@router.get("/{slug}")
async def profile(session: Session, slug: str, response: Response) -> PersonProfile:
    """Header, roles, stats and a plain-language summary built only from stored filings."""
    e = await _entity(session, slug)
    ids = dict((await session.execute(text(
        "SELECT scheme, value FROM entity_ids WHERE entity_id = :id"), {"id": e.id})).tuples().all())
    roles = (await session.execute(text(
        f"SELECT r.id, r.role_type, r.title, r.start_date, r.last_seen_on, o.name, o.slug, o.kind, "
        f"{org_tickers('o')} AS tickers FROM roles r "
        "LEFT JOIN entities o ON o.id = r.org_entity_id WHERE r.entity_id = :id"), {"id": e.id})).all()
    s = (await session.execute(text(f"""
        SELECT count(*) AS trades,
               count(*) FILTER (WHERE t.code = 'P' AND {PERSON_COUNTED}) AS buys,
               count(*) FILTER (WHERE t.code = 'S' AND {PERSON_COUNTED}) AS sells,
               -- values flagged as typing errors on the filing stay out of totals
               sum(t.value) FILTER (WHERE t.code = 'P' AND {PERSON_COUNTED} AND {VALUE_OK}) AS buy_value,
               sum(t.value) FILTER (WHERE t.code = 'S' AND {PERSON_COUNTED} AND {VALUE_OK}) AS sell_value,
               count(*) FILTER (WHERE t.code = 'P' AND {PERSON_COUNTED} AND NOT {VALUE_OK}) AS buys_left_out,
               count(*) FILTER (WHERE t.code = 'S' AND {PERSON_COUNTED} AND NOT {VALUE_OK}) AS sells_left_out,
               min(f.filed_on) AS first_filed, max(f.filed_on) AS last_filed,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY t.delay_days) AS median_delay,
               count(DISTINCT t.filing_id) FILTER (WHERE t.is_late) AS late,
               count(*) FILTER (WHERE t.code = 'S' AND {PERSON_COUNTED} AND t.is_10b5_1) AS s_plan,
               count(*) FILTER (WHERE t.code = 'S' AND {PERSON_COUNTED} AND t.is_10b5_1 IS NOT NULL) AS s_known
        FROM transaction_owners o JOIN transactions t ON t.id = o.transaction_id AND t.is_current
        JOIN filings f ON f.id = t.filing_id WHERE o.entity_id = :id"""), {"id": e.id})).one()
    # "Filings on record here": every Form 3, 4 or 5 naming them as a reporting owner, so a Form 3
    # (holdings when they became an insider, no trades) counts as well as the trades' filings.
    named = (await session.execute(text(
        "SELECT min(f.filed_on), max(f.filed_on) FROM filing_parties fp JOIN filings f ON f.id = fp.filing_id "
        "WHERE fp.entity_id = :id AND fp.role = 'reporting_owner' "
        "AND f.form_type IN ('3', '3/A', '4', '4/A', '5', '5/A')"), {"id": e.id})).one()
    # Form 144 notices they filed as the seller are filings on record too. Many sellers file
    # nothing else: an affiliate who isn't an officer or director has no Form 4 to file
    # (Metropolitan Life Insurance Co, entity 62148: six notices, no Form 3, 4 or 5).
    notices = (await session.execute(text(f"""
        SELECT count(*) AS n, min(f.filed_on) AS first_filed, max(f.filed_on) AS last_filed,
               count(DISTINCT p.issuer_entity_id) AS companies, min(p.issuer_entity_id) AS a_company
        FROM planned_sales p JOIN filings f ON f.id = p.filing_id
        WHERE p.seller_entity_id = :id AND {CURRENT_NOTICE}"""), {"id": e.id})).one()
    notice_company = None
    if notices.companies == 1:  # one company: name it, as the site writes it
        c = (await session.execute(text(
            f"SELECT i.name, {org_tickers('i')} AS tickers FROM entities i WHERE i.id = :id"),
            {"id": notices.a_company})).one()
        notice_company = company_name(c.name, [t for t in c.tickers or () if t])
    first_filed = min((d for d in (s.first_filed, named[0], notices.first_filed) if d is not None), default=None)
    last_filed = max((d for d in (s.last_filed, named[1], notices.last_filed) if d is not None), default=None)
    stats = PersonStats(
        trades=s.trades, open_market_buys=s.buys, open_market_sells=s.sells, buy_value=s.buy_value,
        sell_value=s.sell_value, buys_left_out=s.buys_left_out, sells_left_out=s.sells_left_out,
        first_filed=first_filed, last_filed=last_filed, planned_sales=notices.n,
        median_delay_days=float(s.median_delay) if s.median_delay is not None else None, late_filings=s.late,
        sales_under_10b5_1_pct=round(100 * s.s_plan / s.s_known, 1) if s.s_known else None,
    )
    role_out = [RoleOut(
        organization=entity_link(r.name, r.slug, r.kind, r.tickers or ()) if r.slug else None,
        role_type=r.role_type, title=clean_title(r.title, r.role_type),
        start_date=r.start_date, last_seen_on=r.last_seen_on) for r in sorted(roles, key=_role_rank)]
    # A company or fund as the insider (a 10% owner) reads like the issuer beside it: EDGAR's
    # "BERKSHIRE HATHAWAY INC" is "Berkshire Hathaway Inc" in the sentence.
    subject = e.name if e.kind == "person" else company_name(e.name)
    summary = e.summary or _summary(subject, role_out, stats, sales_under_plan=s.s_plan, sales_answered=s.s_known,
                                    notice_companies=notices.companies, notice_company=notice_company)
    response.headers["Cache-Control"] = "public, max-age=30"
    return PersonProfile(
        entity=entity_link(e.name, e.slug, e.kind), identifiers=ids, tags=list(e.tags or []),
        summary=summary, summary_source=e.summary_source or "template", roles=role_out, stats=stats,
    )


def _role_rank(r: Any) -> tuple[int, int, int, int]:
    """Sort key for a person's roles (rows with id, role_type, title and last_seen_on), best
    first: LEAD_ROLE_ORDER in Python, so the summary introduces a person by the same role
    as the People directory and the search palette. Officers first (the most senior titles
    at the top), then directors, 10% owners, others; within a rank the most recently seen
    (a role never seen sorts after the dated ones), then the newest row."""
    kind_rank = {"officer": 0, "director": 1, "ten_pct_owner": 2}.get(r.role_type, 3)
    top = title_rank(r.title) if kind_rank == 0 else 0
    seen = -r.last_seen_on.toordinal() if r.last_seen_on else 0  # ordinals are positive: None sorts last
    return kind_rank, top, seen, -r.id


def _sentence(text: str) -> str:
    """End with exactly one period ("NIKE, Inc." must not become "NIKE, Inc..")."""
    text = text.rstrip()
    return text if text.endswith(".") else text + "."


def _role_clause(name: str, role: RoleOut, company: str) -> str:
    """ "Tim Cook is the Chief Executive Officer of Apple Inc", "… is a Director of Fifth Third
    Bancorp", "… is a 10% owner of …". A role the filer marked "Other" names no post, so
    it reads "… is an insider of …", with the filer's own description in brackets."""
    if role.role_type == "other_insider":
        described = "" if role.title == ROLE_LABELS["other_insider"] else f" ({role.title})"
        return f"{name} is an insider of {company}{described}"
    article, title = title_in_sentence(role.title, role.role_type)
    return f"{name} is {article} {title} of {company}"


def _notices_clause(s: PersonStats, companies: int, company: str | None) -> str:
    """ "6 Form 144 notices of planned sales of MetLife Inc shares", or "… in 3 companies"."""
    clause = f"{s.planned_sales} Form 144 {'notice' if s.planned_sales == 1 else 'notices'} of planned sales"
    if company:
        return f"{clause} of {company} shares"
    return f"{clause} in {companies} companies" if companies > 1 else clause


def _summary(name: str, roles: list[RoleOut], s: PersonStats, *,
             sales_under_plan: int = 0, sales_answered: int = 0,
             notice_companies: int = 0, notice_company: str | None = None) -> str:
    """A factual sentence built only from stored data (spec §7). No motives, no adjectives.

    `roles` arrive ranked (see _role_rank), with titles and company names already
    cleaned. Counts are "on file" here: they cover the history this site holds, not
    the person's whole career. `sales_answered` counts the open-market sales whose
    filing says whether a Rule 10b5-1 plan was used, `sales_under_plan` those that say yes.
    `notice_companies` is how many companies their Form 144 notices are about, and
    `notice_company` that company's name when there is exactly one.
    """
    parts: list[str] = []
    org = roles[0].organization if roles else None
    if org is not None:
        first = roles[0]
        since = next((r.start_date for r in roles if r.start_date and r.organization == org), None)
        sentence = _role_clause(name, first, org.name)
        if since:
            sentence += f", an insider since {since:%B %Y} (first Form 3)"
        parts.append(_sentence(sentence))
    if s.trades:
        detail = f"{s.trades} reported {'transaction' if s.trades == 1 else 'transactions'} on file"
        market = [f"{n} open-market {word if n == 1 else word + 's'}"
                  for n, word in ((s.open_market_buys, "purchase"), (s.open_market_sells, "sale")) if n]
        if market:
            detail += ", including " + " and ".join(market)
        parts.append(_sentence(detail))
        if s.open_market_sells and (plan := plan_sentence(sales_under_plan, sales_answered, s.open_market_sells)):
            parts.append(plan)
    if s.planned_sales:
        notices = _notices_clause(s, notice_companies, notice_company)
        if parts:
            parts.append(_sentence(f"Also on file: {notices}"))
        else:
            # A seller who files only Form 144s: say what is here and what isn't.
            parts.append(_sentence(f"{name} has filed {notices}"))
            parts.append("No Form 3, 4 or 5 on record here.")
    return " ".join(parts) or f"{name} has filings on record but no reported transactions yet."


@router.get("/{slug}/trades")
async def trades(
    session: Session,
    slug: str,
    response: Response,
    codes: Annotated[str | None, Query(description="'P,S' (default) or 'all'")] = "P,S",
    cursor_: Annotated[str | None, Query(alias="cursor")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> TradePage:
    """This person's trades (including joint filings), newest trade date first."""
    e = await _entity(session, slug)
    where = ["t.is_current", "t.id IN (SELECT transaction_id FROM transaction_owners WHERE entity_id = :eid)"]
    params: dict[str, Any] = {"eid": e.id, "limit": limit + 1}
    code_list = parse_codes(codes)
    if code_list is not None:
        where.append("t.code = ANY(:codes)")
        params["codes"] = code_list
    after = cursor.decode_date_id(cursor_)
    if after:
        where.append("(coalesce(t.traded_on, DATE '1900-01-01'), t.id) < (:c_day, :c_id)")
        params["c_day"], params["c_id"] = after
    # in_totals follows this person's own totals (PERSON_COUNTED), the rule the profile stats use.
    rows = (await session.execute(text(PERSON_TRADE_SQL.format(
        where=" AND ".join(where), order="coalesce(t.traded_on, DATE '1900-01-01') DESC, t.id DESC")),
        params)).all()
    items = [trade_row(r) for r in rows[:limit]]
    nxt = None
    if len(rows) > limit and items:
        last = items[-1]
        nxt = cursor.encode(last.traded_on or date(1900, 1, 1), last.id)
    response.headers["Cache-Control"] = "public, max-age=30"
    return TradePage(items=items, next_cursor=nxt)


@router.get("/{slug}/planned-sales")
async def planned_sales(session: Session, slug: str, response: Response) -> list[PlannedSaleOut]:
    """Form 144 notices this person or organization filed as the seller, newest first, each
    with the company whose shares it is about (and its ticker, when it has a ticker page)."""
    e = await _entity(session, slug)
    rows = (await session.execute(text(f"""
        SELECT p.id, f.source_key, f.url, f.filed_on, p.relationships, p.shares, p.market_value,
               p.approx_sale_on, p.status, p.matched_shares, p.plan_adoption_dates,
               i.name AS issuer_name, i.slug AS issuer_slug, i.kind AS issuer_kind,
               {org_tickers("i")} AS issuer_tickers, sec.primary_ticker AS ticker
        FROM planned_sales p JOIN filings f ON f.id = p.filing_id
        LEFT JOIN entities i ON i.id = p.issuer_entity_id
        LEFT JOIN securities sec ON sec.id = p.security_id
        WHERE p.seller_entity_id = :id AND {CURRENT_NOTICE}
        ORDER BY f.filed_on DESC, p.id DESC LIMIT 200"""), {"id": e.id})).all()
    response.headers["Cache-Control"] = "public, max-age=30"
    return [PlannedSaleOut(
        id=r.id, accession=r.source_key, filing_url=r.url, filed_on=r.filed_on,
        seller=entity_link(e.name, e.slug, e.kind),
        issuer=(entity_link(r.issuer_name, r.issuer_slug, r.issuer_kind, r.issuer_tickers or ())
                if r.issuer_slug else None),
        ticker=r.ticker, relationships=form144_relationships(r.relationships),
        shares=r.shares, market_value=r.market_value, approx_sale_on=r.approx_sale_on, status=r.status,
        matched_shares=r.matched_shares, plan_adoption_dates=list(r.plan_adoption_dates or []),
    ) for r in rows]


@router.get("/{slug}/positions")
async def positions(session: Session, slug: str) -> list[PositionSeries]:
    """Reported holdings after each transaction, one series per issuer, class and ownership form."""
    e = await _entity(session, slug)
    rows = (await session.execute(text(f"""
        SELECT i.name, i.slug, i.kind, {org_tickers("i")} AS tickers, t.security_title,
               coalesce(t.nature_text, CASE WHEN t.ownership_nature = 'I' THEN 'Indirect' ELSE 'Direct' END) AS own,
               t.traded_on, t.shares_owned_after, t.line_no, t.id AS tx_id, f.url
        FROM transactions t JOIN entities i ON i.id = t.issuer_entity_id JOIN filings f ON f.id = t.filing_id
        WHERE t.is_current AND NOT t.is_derivative AND t.shares_owned_after IS NOT NULL
          AND t.traded_on IS NOT NULL AND NOT ('date_after_filing' = ANY(t.data_issues))
          AND t.id IN (SELECT transaction_id FROM transaction_owners WHERE entity_id = :eid)
        ORDER BY i.slug, t.security_title, own, t.traded_on, t.filing_id, t.line_no"""), {"eid": e.id})).all()
    series: dict[tuple[str, str, str], PositionSeries] = {}
    for r in rows:
        key = (r.slug, r.security_title or "", r.own)
        if key not in series:
            series[key] = PositionSeries(issuer=entity_link(r.name, r.slug, r.kind, r.tickers or ()),
                                         security_title=r.security_title or "", ownership=r.own, points=[])
        pts = series[key].points
        # Several rows on one day: the last reported "owned after" is that day's position.
        point = PositionPoint(day=r.traded_on, shares=r.shares_owned_after, transaction_id=r.tx_id,
                              filing_url=r.url)
        if pts and pts[-1].day == r.traded_on:
            pts[-1] = point
        else:
            pts.append(point)
    return list(series.values())
