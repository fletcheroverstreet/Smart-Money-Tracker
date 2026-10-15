"""The Live feed: every new disclosure, newest first, plus today's summary strip."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import text

from smt.api import cursor
from smt.api.deps import Session
from smt.api.queries import COUNTED, FEED_SELECT, VALUE_OK, entity_link, feed_item, parse_codes
from smt.api.schemas import (
    FeedEventDetail,
    FeedPage,
    FootnoteOut,
    HoldingOut,
    PlannedSaleMatchOut,
    PlannedSaleOut,
    TodayEntry,
    TodaySummary,
)
from smt.api.wording import company_name, form144_relationships, relationship_role
from smt.domain.business_days import ET, is_sec_business_day

router = APIRouter(prefix="/api/feed", tags=["feed"])


@router.get("")
async def feed(
    session: Session,
    response: Response,
    cursor_: Annotated[str | None, Query(alias="cursor")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    types: Annotated[str | None, Query(description="comma list: insider_trade,planned_sale,insider_holding")] = None,
    codes: Annotated[str | None, Query(description="Form 4 codes, e.g. 'P,S' (default) or 'all'")] = "P,S",
    min_size: Annotated[float | None, Query(ge=0, description="minimum dollar size")] = None,
    ticker: str | None = None,
) -> FeedPage:
    """Live feed, keyset-paged by (detected_at, id) descending.

    By default only open-market buys and sells (codes P and S) are shown among
    insider trades; pass codes=all to include grants, exercises and the rest.
    """
    where = ["e.is_live"]
    params: dict[str, object] = {"limit": limit + 1}
    if types:
        where.append("e.event_type = ANY(:types)")
        params["types"] = [t.strip() for t in types.split(",") if t.strip()]
    code_list = parse_codes(codes)
    if code_list is not None:
        where.append("(e.event_type <> 'insider_trade' OR e.payload->>'code' = ANY(:codes))")
        params["codes"] = code_list
    if min_size is not None:
        where.append("coalesce(e.size_usd, 0) >= :min_size")
        params["min_size"] = min_size
    if ticker:
        where.append("e.security_id IN (SELECT security_id FROM security_ids WHERE scheme = 'ticker' "
                     "AND value = :ticker)")
        params["ticker"] = ticker.upper()
    after = cursor.decode_timestamp_id(cursor_)
    if after:
        where.append("(e.detected_at, e.id) < (:c_at, :c_id)")
        params["c_at"], params["c_id"] = after
    rows = (await session.execute(text(
        FEED_SELECT + " WHERE " + " AND ".join(where) +
        " ORDER BY e.detected_at DESC, e.id DESC LIMIT :limit"), params)).all()
    items = [feed_item(r) for r in rows[:limit]]
    next_cursor = cursor.encode(items[-1].detected_at, items[-1].id) if len(rows) > limit and items else None
    response.headers["Cache-Control"] = "public, max-age=5"
    return FeedPage(items=items, next_cursor=next_cursor)


@router.get("/events/{event_id}")
async def event_detail(session: Session, event_id: int, response: Response) -> FeedEventDetail:
    """One feed event in full, for the quick look (Form 144 notices and Form 3 statements;
    a trade's own detail is /api/trades/{id})."""
    r = (await session.execute(text(FEED_SELECT + " WHERE e.id = :id"), {"id": event_id})).first()
    if r is None:
        raise HTTPException(status_code=404, detail="No such event")
    item = feed_item(r)
    f = (await session.execute(text(
        "SELECT f.id, f.source_key, f.url, f.filed_on, f.accepted_at FROM feed_events e "
        "JOIN filings f ON f.id = e.filing_id WHERE e.id = :id"), {"id": event_id})).one()
    detail = FeedEventDetail(item=item, accession=f.source_key, filing_url=f.url, filed_on=f.filed_on,
                             accepted_at=f.accepted_at)
    if item.event_type == "planned_sale":
        # The notice as it now stands. When a 144/A has replaced this filing, the amendment's
        # figures and Form 4 links are the current ones (the replaced version keeps none; see
        # smt/ingest/form144.py), so they are shown, under the amendment's accession. The quick
        # look notices the accession differs from the event's and says the notice was amended.
        p = (await session.execute(text("""
            SELECT p.id, cf.source_key, cf.url, cf.filed_on, o.name, o.slug, o.kind, p.relationships, p.shares,
                   p.market_value, p.approx_sale_on, p.status, p.matched_shares, p.plan_adoption_dates
            FROM filings f JOIN planned_sales p ON p.filing_id = coalesce(f.superseded_by_id, f.id)
            JOIN filings cf ON cf.id = p.filing_id JOIN entities o ON o.id = p.seller_entity_id
            WHERE f.id = :f"""), {"f": f.id})).first()
        if p is not None:
            detail.planned_sale = PlannedSaleOut(
                id=p.id, accession=p.source_key, filing_url=p.url, filed_on=p.filed_on,
                seller=entity_link(p.name, p.slug, p.kind), relationships=form144_relationships(p.relationships),
                shares=p.shares, market_value=p.market_value, approx_sale_on=p.approx_sale_on, status=p.status,
                matched_shares=p.matched_shares, plan_adoption_dates=list(p.plan_adoption_dates or []))
            detail.matches = [PlannedSaleMatchOut(transaction_id=m.transaction_id, traded_on=m.traded_on,
                                                  shares=m.shares, price=m.price, confidence=m.confidence)
                              for m in (await session.execute(text("""
                SELECT m.transaction_id, t.traded_on, t.shares, t.price, m.confidence
                FROM planned_sale_matches m JOIN transactions t ON t.id = m.transaction_id
                WHERE m.planned_sale_id = :p ORDER BY t.traded_on, t.id"""), {"p": p.id})).all()]
    elif item.event_type == "insider_holding":
        # Table I (shares) first, then Table II (derivatives, with their terms as filed).
        holdings = (await session.execute(text(
            "SELECT security_title, is_derivative, shares, ownership_nature, nature_text, underlying_title, "
            "underlying_shares, exercise_price, exercise_date, expiration_date, footnote_ids "
            "FROM insider_holdings WHERE filing_id = :f ORDER BY table_name DESC, line_no"), {"f": f.id})).all()
        detail.holdings = [HoldingOut(
            security_title=h.security_title, is_derivative=h.is_derivative, shares=h.shares,
            ownership=h.nature_text or ("Indirect" if h.ownership_nature == "I" else "Direct"),
            underlying_title=h.underlying_title, underlying_shares=h.underlying_shares,
            exercise_price=h.exercise_price, exercise_date=h.exercise_date, expiration_date=h.expiration_date,
            footnote_ids=list(h.footnote_ids or [])) for h in holdings]
        # The footnotes those lines point at (often the vesting dates of each grant), in first-use order.
        notes = (await session.execute(text("SELECT footnotes FROM filings WHERE id = :f"),
                                       {"f": f.id})).scalar_one() or {}
        used = dict.fromkeys(fid for h in detail.holdings for fid in h.footnote_ids if fid in notes)
        detail.holding_footnotes = [FootnoteOut(id=fid, text=str(notes[fid])) for fid in used]
    response.headers["Cache-Control"] = "public, max-age=60"
    return detail


#: How many entries each of the strip's lists shows (the Methodology page says five).
TODAY_TOP = 5

#: Today's largest open-market buys (or sells), one entry per owner and company.
#: The trades are exactly the ones every total counts (COUNTED, VALUE_OK: current, open
#: market, one copy per trade, a usable dollar value), each under its attributed owner.
#: The trades of one owner in one company (a sale reported as several lots at different
#: prices, or several filings that day) add up to one entry, ranked by their total.
_TODAY_SQL = f"""
WITH lots AS (
    SELECT t.id, t.entity_id, t.issuer_entity_id, t.value, t.shares, t.traded_on
    FROM transactions t JOIN filings f ON f.id = t.filing_id
    WHERE f.filed_on = :day AND t.direction = :dir AND {COUNTED} AND {VALUE_OK} AND t.value IS NOT NULL
), entries AS (
    SELECT entity_id, issuer_entity_id, sum(value) AS total, count(*) AS lots,
           -- a total share count only when every lot gives one (a missing number isn't zero)
           CASE WHEN count(shares) = count(*) THEN sum(shares) END AS shares,
           array_agg(value ORDER BY value DESC, id) AS lot_values,
           (array_agg(id ORDER BY value DESC, id))[1] AS lead_id,
           min(traded_on) AS first_traded_on, max(traded_on) AS last_traded_on
    FROM lots GROUP BY entity_id, issuer_entity_id
    ORDER BY total DESC, lead_id LIMIT :limit
)
SELECT e.total, e.lots, e.shares, e.lot_values, e.lead_id, e.first_traded_on, e.last_traded_on,
       s.primary_ticker, i.name AS issuer, o.name, o.slug, o.kind,
       -- the owner's role as given on the largest lot's filing
       (SELECT fp.relationship FROM filing_parties fp WHERE fp.filing_id = t.filing_id
        AND fp.entity_id = t.entity_id AND fp.role = 'reporting_owner') AS rel
FROM entries e
JOIN transactions t ON t.id = e.lead_id
JOIN entities o ON o.id = e.entity_id
LEFT JOIN entities i ON i.id = e.issuer_entity_id
LEFT JOIN securities s ON s.id = t.security_id
ORDER BY e.total DESC, e.lead_id
"""


async def today_entries(session: Session, day: date, direction: str, limit: int = TODAY_TOP) -> list[TodayEntry]:
    """The largest open-market buys ("buy") or sells ("sell") disclosed on `day`, grouped
    by owner and company (see _TODAY_SQL), largest total first."""
    rows = (await session.execute(text(_TODAY_SQL), {"day": day, "dir": direction, "limit": limit})).all()
    return [TodayEntry(
        # The issuer's ticker keeps a name such as "ACNB CORP" reading "ACNB Corp" (a missing one is skipped).
        ticker=r.primary_ticker, issuer=company_name(r.issuer, [r.primary_ticker]) if r.issuer else "",
        # A fund or company as the owner reads like the issuer beside it ("Berkshire Hathaway Inc").
        owner=entity_link(r.name, r.slug, r.kind),
        role=relationship_role(r.rel),
        value=r.total, shares=r.shares, lots=r.lots, lot_values=list(r.lot_values),
        largest_transaction_id=r.lead_id, first_traded_on=r.first_traded_on, last_traded_on=r.last_traded_on,
    ) for r in rows]


def strip_day(today: date) -> date:
    """The day the today strip covers: `today` if it is an SEC business day, otherwise the
    latest business day before it.

    EDGAR takes no filings on weekends and federal holidays (Reg S-T Rule 13: a filing sent
    then is dated the next business day), so on those days "today" would always be empty.
    The strip shows the last day that had filings instead, and says EDGAR is closed today.
    """
    day = today
    while not is_sec_business_day(day):
        day -= timedelta(days=1)
    return day


@router.get("/today")
async def today(session: Session, response: Response) -> TodaySummary:
    """Today's strip: largest open-market buys and sells, filings by form (US Eastern date).
    On a weekend or SEC holiday it covers the latest business day (see strip_day)."""
    today_et = datetime.now(ET).date()
    day = strip_day(today_et)
    by_form = dict((await session.execute(text(
        "SELECT form_type, count(*) FROM filings WHERE filed_on = :day GROUP BY form_type"),
        {"day": day})).tuples().all())
    planned = (await session.execute(text(
        # Only the current version of each notice: a 144/A replaces the notice it amends.
        "SELECT count(*) FROM planned_sales p JOIN filings f ON f.id = p.filing_id "
        "WHERE f.filed_on = :day AND f.superseded_by_id IS NULL"),
        {"day": day})).scalar_one()
    buyers = (await session.execute(text(
        "SELECT count(DISTINCT t.entity_id) FROM transactions t JOIN filings f ON f.id = t.filing_id "
        f"WHERE f.filed_on = :day AND t.code = 'P' AND {COUNTED}"), {"day": day})).scalar_one()
    response.headers["Cache-Control"] = "public, max-age=15"
    return TodaySummary(day=day, edgar_open_today=(day == today_et),
                        largest_buys=await today_entries(session, day, "buy"),
                        largest_sells=await today_entries(session, day, "sell"),
                        filings_by_form={str(k): int(v) for k, v in by_form.items()},
                        planned_sales=int(planned), insiders_buying=int(buyers))
