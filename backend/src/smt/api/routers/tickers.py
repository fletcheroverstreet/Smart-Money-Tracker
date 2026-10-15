"""Ticker pages: one stock, every smart-money event on it.

Insider activity is issuer-level: a Form 4 names the issuer, and its rows are
linked to the issuer's primary security. A company with several share
classes shows the same insider activity on each class's page.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import text

from smt.api import cursor
from smt.api.deps import Session
from smt.api.queries import COUNTED, TRADE_SQL, entity_link, parse_codes, trade_row
from smt.api.schemas import (
    EntityLink,
    PlannedSaleOut,
    SecurityLink,
    TickerActivityRow,
    TickerIndexPage,
    TickerProfile,
    TimelineEvent,
    TradePage,
)
from smt.api.wording import company_name, form144_relationships
from smt.domain.business_days import ET
from smt.domain.codes import CODE_SHORT
from smt.ingest.quality import DATE_AFTER_FILING, VALUE_IMPLAUSIBLE

router = APIRouter(prefix="/api/tickers", tags=["tickers"])


async def _security(session: Session, symbol: str) -> Any:
    row = (await session.execute(text("""
        SELECT s.id, s.name, s.primary_ticker, s.sector, s.sector_source, s.exchange, s.issuer_entity_id,
               i.name AS issuer_name, i.slug AS issuer_slug, i.kind AS issuer_kind
        FROM security_ids x JOIN securities s ON s.id = x.security_id
        LEFT JOIN entities i ON i.id = s.issuer_entity_id
        WHERE x.scheme = 'ticker' AND x.value = :t
        ORDER BY x.valid_to DESC NULLS FIRST LIMIT 1"""), {"t": symbol.upper()})).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"No security with ticker {symbol.upper()}")
    return row


def _today() -> date:
    """Today's date in US Eastern time, the calendar EDGAR and the markets run on.

    A module-level function so tests can pin "today" without touching the clock.
    """
    return datetime.now(ET).date()


def _like_prefix(q: str) -> str:
    """A LIKE pattern matching strings that start with `q`, with q's own wildcards escaped."""
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"{escaped}%"


#: ORDER BY for each `sort` value. Every ranking is "largest first" and ends with
#: unique keys (ticker, then issuer id), so offset paging never repeats or skips a row.
_INDEX_ORDER = {
    "activity": "(r.buys + r.sells) DESC, r.insiders DESC, r.last_traded_on DESC NULLS LAST",
    "buys": "r.buy_value DESC NULLS LAST, r.buys DESC",
    "sells": "r.sell_value DESC NULLS LAST, r.sells DESC",
    "net": "r.net_value DESC NULLS LAST",
}


# Declared before "/{symbol}" so the bare path is never read as a ticker symbol.
@router.get("")
async def ticker_index(
    session: Session,
    response: Response,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
    sort: Annotated[str, Query(pattern="^(activity|buys|sells|net)$")] = "activity",
    q: Annotated[str | None, Query(max_length=40)] = None,
    cursor_: Annotated[str | None, Query(alias="cursor")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> TickerIndexPage:
    """Companies ranked by open-market insider trading over the last `days` days.

    What is counted, and why:

    * Only **open-market purchases and sales** (Form 4/5 codes P and S). Awards,
      option exercises, tax withholding and gifts are left out of every column,
      because none of them is a decision to buy or sell at the market price.
    * Only **current** rows. When an amendment (4/A) corrects a trade, the
      corrected row counts and the replaced one doesn't, so nothing counts twice.
    * The window runs on the **trade date**, from `since` to today (US Eastern),
      both inclusive. A trade dated in the future (a typo in a filing) isn't in
      "the last N days", so it is left out.
    * Dollar sums add the values reported on the filings. Two kinds of trade
      still count as trades but add nothing to the sums, so the sums are lower
      bounds when either count is above zero:
      - `trades_without_price`: the filing gives no price, so there is no value;
      - `trades_left_out` (per side `buys_left_out`, `sells_left_out`): the
        filing gives a price, but it isn't a dollar price per share: another
        currency (UMC's Form 4s state New Taiwan dollars), a foreign listing
        whose currency isn't stated (Bradesco's BBDC4), or a figure that can't
        be right as filed (smt/ingest/quality.py flags all three
        `value_implausible`). These did report a price, so calling them
        "no price" would be wrong.
      A side with no trades has a null sum (shown as a dash, never $0).
    * `insiders` counts the owner each trade is attributed to. A joint filing
      (a fund with its general partner and manager) counts once.

    Insider activity is issuer-level, like the ticker page itself: a company
    with several share classes appears once, under the ticker its insiders'
    filings use most. Companies with no ticker are left out.

    `sort`: activity (number of trades), buys (purchase value), sells (sale
    value) or net (purchases minus sales). `q` keeps tickers or company names
    that start with it. Paging is by offset (`next_cursor`), because the ranking
    keys are aggregates that change as filings arrive.
    """
    until = _today()
    since = until - timedelta(days=days - 1)
    params: dict[str, Any] = {"since": since, "until": until, "limit": limit + 1,
                              "bad_value": VALUE_IMPLAUSIBLE, "bad_date": DATE_AFTER_FILING}
    params["offset"] = cursor.decode_offset(cursor_)
    search = ""
    if q and q.strip():
        search = "AND (s.primary_ticker LIKE :ticker_prefix ESCAPE '\\' OR s.name ILIKE :name_prefix ESCAPE '\\')"
        params["ticker_prefix"] = _like_prefix(q.strip().upper())
        params["name_prefix"] = _like_prefix(q.strip())
    rows = (await session.execute(text(f"""
        WITH act AS (
            SELECT t.issuer_entity_id,
                   count(*) FILTER (WHERE t.code = 'P') AS buys,
                   sum(t.value) FILTER (WHERE t.code = 'P' AND NOT (:bad_value = ANY(t.data_issues)))
                     AS buy_value,
                   count(*) FILTER (WHERE t.code = 'S') AS sells,
                   sum(t.value) FILTER (WHERE t.code = 'S' AND NOT (:bad_value = ANY(t.data_issues)))
                     AS sell_value,
                   -- two disjoint reasons a counted trade adds nothing to the sums: the filing
                   -- gives no price (value IS NULL), or its price isn't a dollar price per share
                   -- (flagged value_implausible: another currency, or a figure that can't be right)
                   count(*) FILTER (WHERE t.value IS NULL AND NOT (:bad_value = ANY(t.data_issues)))
                     AS without_price,
                   count(*) FILTER (WHERE t.code = 'P' AND :bad_value = ANY(t.data_issues)) AS buys_left_out,
                   count(*) FILTER (WHERE t.code = 'S' AND :bad_value = ANY(t.data_issues)) AS sells_left_out,
                   count(DISTINCT t.entity_id) AS insiders,
                   max(t.traded_on) FILTER (WHERE NOT (:bad_date = ANY(t.data_issues))) AS last_traded_on,
                   -- the security these trades point at most often (nulls ignored)
                   mode() WITHIN GROUP (ORDER BY t.security_id) AS main_security_id
            FROM transactions t
            WHERE {COUNTED} AND t.source IN ('form4', 'form5')
              AND t.traded_on BETWEEN :since AND :until AND t.issuer_entity_id IS NOT NULL
            GROUP BY t.issuer_entity_id
        ), r AS (
            SELECT act.*, s.primary_ticker, s.name AS security_name, s.sector,
                   i.name AS issuer_name, i.slug AS issuer_slug, i.kind AS issuer_kind,
                   -- a side with no trades contributes 0; a side whose trades all lack
                   -- values makes the net unknown (NULL), never a made-up number
                   (CASE WHEN act.buys = 0 THEN 0 ELSE act.buy_value END)
                     - (CASE WHEN act.sells = 0 THEN 0 ELSE act.sell_value END) AS net_value
            FROM act
            JOIN LATERAL (
                SELECT s.primary_ticker, s.name, s.sector FROM securities s
                WHERE s.issuer_entity_id = act.issuer_entity_id AND s.primary_ticker IS NOT NULL
                ORDER BY coalesce(s.id = act.main_security_id, false) DESC, s.is_active DESC, s.id
                LIMIT 1
            ) s ON true
            JOIN entities i ON i.id = act.issuer_entity_id
            WHERE true {search}
        )
        SELECT * FROM r
        ORDER BY {_INDEX_ORDER[sort]}, r.primary_ticker, r.issuer_entity_id
        LIMIT :limit OFFSET :offset"""), params)).all()
    items = [TickerActivityRow(
        # EDGAR's all-capitals company names read as "United Microelectronics Corp" (company_name).
        security=SecurityLink(ticker=r.primary_ticker, name=_display_name(r.security_name, r.primary_ticker),
                              sector=r.sector),
        issuer=EntityLink(name=_display_name(r.issuer_name, r.primary_ticker), slug=r.issuer_slug,
                          kind=r.issuer_kind),
        buys=r.buys, buy_value=r.buy_value if r.buys else None,
        sells=r.sells, sell_value=r.sell_value if r.sells else None,
        net_value=r.net_value, insiders=r.insiders, last_traded_on=r.last_traded_on,
        trades_without_price=r.without_price, trades_left_out=r.buys_left_out + r.sells_left_out,
        buys_left_out=r.buys_left_out, sells_left_out=r.sells_left_out,
    ) for r in rows[:limit]]
    response.headers["Cache-Control"] = "public, max-age=60"
    more = len(rows) > limit
    return TickerIndexPage(
        items=items, next_cursor=cursor.encode(params["offset"] + limit) if more else None,
        days=days, since=since, until=until,
    )



def _display_name(name: str | None, ticker: str | None) -> str | None:
    """A company name as shown (company_name), or None when there is none."""
    return company_name(name, [ticker] if ticker else []) if name else name

@router.get("/{symbol}")
async def ticker(session: Session, symbol: str, response: Response) -> TickerProfile:
    s = await _security(session, symbol)
    ids = dict((await session.execute(text(
        "SELECT scheme, value FROM security_ids WHERE security_id = :id AND valid_to IS NULL"),
        {"id": s.id})).tuples().all())
    # The same 90-day window as the tickers list (ticker_index): the last 90 days in New York,
    # today included, and nothing dated after today, so the two pages show the same counts.
    until = _today()
    since = until - timedelta(days=90 - 1)
    window = "t.traded_on BETWEEN :since AND :until AND t.source IN ('form4', 'form5')"
    c = (await session.execute(text(f"""
        SELECT count(*) AS trades,
               count(*) FILTER (WHERE t.code = 'P' AND {window} AND {COUNTED}) AS buys,
               count(*) FILTER (WHERE t.code = 'S' AND {window} AND {COUNTED}) AS sells,
               max(f.filed_on) AS last_filed
        FROM transactions t JOIN filings f ON f.id = t.filing_id
        WHERE t.is_current AND t.issuer_entity_id = :issuer"""),
        {"issuer": s.issuer_entity_id, "since": since, "until": until})).one()
    # Open Form 144 notices. A notice replaced by a 144/A is counted once, through its
    # amendment: the replaced version stays 'open' with no links (smt/ingest/form144.py).
    open_sales = (await session.execute(text(
        "SELECT count(*) FROM planned_sales p JOIN filings f ON f.id = p.filing_id "
        "WHERE p.issuer_entity_id = :issuer AND p.status = 'open' AND f.superseded_by_id IS NULL"),
        {"issuer": s.issuer_entity_id})).scalar_one()
    response.headers["Cache-Control"] = "public, max-age=30"
    return TickerProfile(
        security=SecurityLink(ticker=s.primary_ticker, name=_display_name(s.name, s.primary_ticker),
                              sector=s.sector),
        issuer=EntityLink(name=_display_name(s.issuer_name, s.primary_ticker), slug=s.issuer_slug,
                          kind=s.issuer_kind) if s.issuer_slug else None,
        sector_source=s.sector_source, exchange=s.exchange, identifiers=ids, insider_trades=c.trades,
        open_market_buys_90d=c.buys, open_market_sells_90d=c.sells, planned_sales_open=open_sales,
        last_filed=c.last_filed,
    )


@router.get("/{symbol}/events")
async def events(
    session: Session,
    symbol: str,
    response: Response,
    start: date | None = None,
    end: date | None = None,
    all_codes: bool = False,
) -> list[TimelineEvent]:
    """Markers for the event timeline: insider trades and planned sales, by trade date."""
    s = await _security(session, symbol)
    params: dict[str, Any] = {"issuer": s.issuer_entity_id, "start": start or date(1990, 1, 1),
                              "end": end or date(2100, 1, 1), "bad_value": VALUE_IMPLAUSIBLE,
                              "bad_date": DATE_AFTER_FILING}
    # One marker per trade: second copies of a trade and unpaired amendment rows are left out.
    code_filter = ("AND t.duplicate_of_id IS NULL AND NOT ('amendment_unpaired' = ANY(t.data_issues))"
                   + ("" if all_codes else " AND t.is_open_market"))
    trades = (await session.execute(text(f"""
        SELECT t.id, t.traded_on, t.direction, t.code, o.name, f.url,
               -- a value flagged as a typing error would dwarf every other marker: size it as unknown
               CASE WHEN :bad_value = ANY(t.data_issues) THEN NULL ELSE t.value END AS value
        FROM transactions t JOIN filings f ON f.id = t.filing_id JOIN entities o ON o.id = t.entity_id
        WHERE t.is_current AND t.issuer_entity_id = :issuer AND t.traded_on BETWEEN :start AND :end
          AND NOT (:bad_date = ANY(t.data_issues))
        {code_filter}
        ORDER BY t.traded_on"""), params)).all()
    out = [TimelineEvent(
        layer="insiders", day=r.traded_on, direction=r.direction,
        label=f"{r.name}: {CODE_SHORT.get(r.code or '', r.code or '')}", value=float(r.value) if r.value else None,
        transaction_id=r.id, filing_url=r.url) for r in trades]
    planned = (await session.execute(text("""
        SELECT p.approx_sale_on, p.market_value, o.name, f.url
        FROM planned_sales p JOIN filings f ON f.id = p.filing_id JOIN entities o ON o.id = p.seller_entity_id
        WHERE p.issuer_entity_id = :issuer AND p.approx_sale_on BETWEEN :start AND :end
          AND f.superseded_by_id IS NULL"""), params)).all()
    out += [TimelineEvent(layer="planned_sales", day=r.approx_sale_on, direction="planned_sale",
                          label=f"{r.name}: intends to sell", value=float(r.market_value) if r.market_value else None,
                          transaction_id=None, filing_url=r.url) for r in planned]
    response.headers["Cache-Control"] = "public, max-age=60"
    return sorted(out, key=lambda e: e.day)


@router.get("/{symbol}/insiders")
async def insider_trades(
    session: Session,
    symbol: str,
    response: Response,
    codes: str | None = "P,S",
    cursor_: Annotated[str | None, Query(alias="cursor")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> TradePage:
    s = await _security(session, symbol)
    where = ["t.is_current", "t.issuer_entity_id = :issuer"]
    params: dict[str, Any] = {"issuer": s.issuer_entity_id, "limit": limit + 1}
    code_list = parse_codes(codes)
    if code_list is not None:
        where.append("t.code = ANY(:codes)")
        params["codes"] = code_list
    after = cursor.decode_date_id(cursor_)
    if after:
        where.append("(coalesce(t.traded_on, DATE '1900-01-01'), t.id) < (:c_day, :c_id)")
        params["c_day"], params["c_id"] = after
    rows = (await session.execute(text(TRADE_SQL.format(
        where=" AND ".join(where), order="coalesce(t.traded_on, DATE '1900-01-01') DESC, t.id DESC")),
        params)).all()
    items = [trade_row(r) for r in rows[:limit]]
    nxt = cursor.encode(items[-1].traded_on or date(1900, 1, 1), items[-1].id) if len(rows) > limit and items else None
    response.headers["Cache-Control"] = "public, max-age=30"
    return TradePage(items=items, next_cursor=nxt)


@router.get("/{symbol}/planned-sales")
async def planned_sales(session: Session, symbol: str) -> list[PlannedSaleOut]:
    """Form 144 notices for this issuer, newest first."""
    s = await _security(session, symbol)
    rows = (await session.execute(text("""
        SELECT p.id, f.source_key, f.url, f.filed_on, o.name, o.slug, o.kind, p.relationships, p.shares,
               p.market_value, p.approx_sale_on, p.status, p.matched_shares, p.plan_adoption_dates
        FROM planned_sales p JOIN filings f ON f.id = p.filing_id JOIN entities o ON o.id = p.seller_entity_id
        WHERE p.issuer_entity_id = :issuer AND f.superseded_by_id IS NULL
        ORDER BY f.filed_on DESC, p.id DESC LIMIT 200"""), {"issuer": s.issuer_entity_id})).all()
    return [PlannedSaleOut(
        id=r.id, accession=r.source_key, filing_url=r.url, filed_on=r.filed_on,
        seller=entity_link(r.name, r.slug, r.kind), relationships=form144_relationships(r.relationships),
        shares=r.shares, market_value=r.market_value, approx_sale_on=r.approx_sale_on, status=r.status,
        matched_shares=r.matched_shares, plan_adoption_dates=list(r.plan_adoption_dates or []),
    ) for r in rows]
