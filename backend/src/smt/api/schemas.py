"""API response models. The front end's TypeScript types are generated from these
(OpenAPI -> openapi-typescript), so a change here shows up as a type error in
the UI instead of a silent mismatch."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class EntityLink(BaseModel):
    """A person/company reference suitable for a link."""

    name: str
    slug: str
    kind: str


class SecurityLink(BaseModel):
    ticker: str | None
    name: str
    sector: str | None = None


class FeedItem(BaseModel):
    """One Live-feed row: display-ready, no joins needed on the client."""

    id: int
    event_type: str = Field(description="insider_trade | planned_sale | insider_holding")
    form_type: str
    section: str
    detected_at: datetime
    disclosed_at: datetime | None
    occurred_on: date | None
    size_usd: float | None
    transaction_id: int | None
    payload: dict[str, Any]


class FeedPage(BaseModel):
    items: list[FeedItem]
    next_cursor: str | None


class TodayEntry(BaseModel):
    """One owner's open-market buying (or selling) in one company, disclosed today.

    A Form 4 often reports one sale as several lots at different prices, and an
    owner may file more than once a day; all of one owner's lots in one company
    are one entry, so the strip lists five different people, not one person's lots.
    """

    ticker: str | None
    issuer: str
    owner: EntityLink
    role: str | None = Field(description="role on the largest lot's filing, e.g. 'Director, 10% owner'")
    value: Decimal = Field(description="total dollar value of the lots")
    shares: Decimal | None = Field(description="total shares; null when a lot doesn't give its share count")
    lots: int = Field(description="how many trades (lots) the entry adds up")
    lot_values: list[Decimal] = Field(description="each lot's dollar value, largest first")
    largest_transaction_id: int = Field(description="the largest lot: the amount opens its quick look")
    first_traded_on: date | None
    last_traded_on: date | None


class TodaySummary(BaseModel):
    """The "today" strip on the Live page (US Eastern date).

    EDGAR takes no filings on weekends and federal holidays, so on those days the strip
    covers the latest SEC business day instead of an empty "today" (see feed.strip_day).
    """

    day: date = Field(description="the day the figures cover: today (US Eastern) on an SEC business day, "
                                  "otherwise the latest SEC business day before today")
    edgar_open_today: bool = Field(description="false on weekends and SEC holidays, when EDGAR accepts no "
                                               "filings (then `day` is the latest business day)")
    largest_buys: list[TodayEntry] = Field(description="up to 5 entries, largest total first")
    largest_sells: list[TodayEntry] = Field(description="up to 5 entries, largest total first")
    filings_by_form: dict[str, int]
    planned_sales: int
    insiders_buying: int


class TradeCopy(BaseModel):
    """Another filing's row for the same trade (see smt.ingest.quality.mark_duplicates)."""

    trade_id: int
    owner: EntityLink = Field(description="the owner that row is filed under")


class TradeRow(BaseModel):
    """A transaction as shown in trade tables (person, ticker)."""

    id: int
    accession: str
    form_type: str
    filing_url: str
    traded_on: date | None
    filed_on: date | None
    accepted_at: datetime | None
    detected_at: datetime
    delay_days: int | None
    is_late: bool | None
    deadline_rule: str | None
    code: str | None
    code_label: str
    direction: str
    is_open_market: bool
    is_derivative: bool
    security_title: str | None
    ticker: str | None
    issuer: EntityLink | None
    owner: EntityLink
    joint_owners: int
    owner_type: str
    shares: Decimal | None
    price: Decimal | None
    price_is_weighted_avg: bool
    value: Decimal | None
    shares_owned_after: Decimal | None
    is_10b5_1: bool | None
    exercise_and_sell: bool
    is_current: bool
    data_issues: list[str] = Field(
        default_factory=list,
        description="values that can't be right as filed: date_after_filing, value_implausible "
                    "(numbers are shown as filed but left out of rankings and totals); "
                    "amendment_unpaired (an amended row that couldn't be matched to the row it "
                    "corrects, so it may repeat it: left out of totals)")
    in_totals: bool = Field(
        description="counted in the totals this row sits next to. On a person's trades list that is "
                    "the person's own totals (smt.api.queries.PERSON_COUNTED); everywhere else the "
                    "site-wide totals (COUNTED). Only open-market buys and sells are ever counted.")
    duplicate_of: TradeCopy | None = Field(
        default=None,
        description="set when another filing reports the same trade (a fund and its general partner, "
                    "or the same trade filed again): the copy that the totals next to this row count "
                    "(on a person's trades list, that person's own totals; elsewhere the site-wide totals). "
                    "The site-wide totals never count this row; `in_totals` says whether the totals "
                    "next to it do.")


class TradePage(BaseModel):
    items: list[TradeRow]
    next_cursor: str | None


class RoleOut(BaseModel):
    organization: EntityLink | None
    role_type: str
    title: str
    start_date: date | None
    last_seen_on: date | None


class PersonStats(BaseModel):
    trades: int
    open_market_buys: int
    open_market_sells: int
    buy_value: Decimal | None
    sell_value: Decimal | None
    buys_left_out: int = Field(0, description="counted buys whose value is left out of buy_value: a figure "
                                              "that can't be right as filed, or a price in another currency")
    sells_left_out: int = Field(0, description="the same for sales and sell_value")
    first_filed: date | None = Field(description="the earliest filing date among the Forms 3, 4 and 5 on "
                                                 "record that name this owner (a Form 3 with no trades counts) "
                                                 "and the Form 144 notices they filed as the seller")
    last_filed: date | None = Field(description="the latest such filing date")
    planned_sales: int = Field(0, description="current Form 144 notices this person or organization filed as "
                                              "the seller (a notice replaced by an amendment isn't counted)")
    median_delay_days: float | None
    late_filings: int
    sales_under_10b5_1_pct: float | None


class PersonProfile(BaseModel):
    entity: EntityLink
    identifiers: dict[str, str]
    tags: list[str]
    summary: str
    summary_source: str
    roles: list[RoleOut]
    stats: PersonStats


class PositionPoint(BaseModel):
    day: date
    shares: Decimal
    transaction_id: int = Field(description="the trade whose 'owned after' figure this is")
    filing_url: str = Field(description="the filing that reported it")


class PositionSeries(BaseModel):
    issuer: EntityLink
    security_title: str
    ownership: str = Field(description="direct, or the indirect holder text")
    points: list[PositionPoint]


class DirectoryRow(BaseModel):
    entity: EntityLink
    role: str | None
    organization: EntityLink | None
    trades: int
    open_market_buys: int
    open_market_sells: int
    last_traded_on: date | None


class DirectoryPage(BaseModel):
    items: list[DirectoryRow]
    next_cursor: str | None


class TickerProfile(BaseModel):
    security: SecurityLink
    issuer: EntityLink | None
    sector_source: str
    exchange: str | None
    identifiers: dict[str, str]
    insider_trades: int
    open_market_buys_90d: int
    open_market_sells_90d: int
    planned_sales_open: int
    last_filed: date | None


class TickerActivityRow(BaseModel):
    """One row of the ticker index: a company's open-market insider trading in the window."""

    security: SecurityLink
    issuer: EntityLink | None
    buys: int = Field(description="open-market purchases (code P)")
    buy_value: Decimal | None = Field(description="sum of reported purchase values; null when there are none")
    sells: int = Field(description="open-market sales (code S)")
    sell_value: Decimal | None = Field(description="sum of reported sale values; null when there are none")
    net_value: Decimal | None = Field(
        description="buy_value minus sell_value; null when a side has trades but no reported values")
    insiders: int = Field(description="distinct insiders the trades are attributed to")
    last_traded_on: date | None
    trades_without_price: int = Field(
        description="trades counted above whose filing gives no price, so they have no value and add nothing "
                    "to the sums (the sums are lower bounds)")
    trades_left_out: int = Field(
        description="trades counted above whose price is stated but left out of the dollar sums: another "
                    "currency, a currency the filing doesn't state, or a figure that can't be right as filed "
                    "(buys_left_out + sells_left_out)")
    buys_left_out: int = Field(description="purchases among trades_left_out")
    sells_left_out: int = Field(description="sales among trades_left_out")


class TickerIndexPage(BaseModel):
    items: list[TickerActivityRow]
    next_cursor: str | None
    days: int
    since: date = Field(description="first trade date in the window (inclusive)")
    until: date = Field(description="last trade date in the window (inclusive), today in US Eastern time")


class TimelineEvent(BaseModel):
    """One marker on the ticker event timeline."""

    layer: str = Field(description="insiders | planned_sales")
    day: date
    direction: str
    label: str
    value: float | None
    transaction_id: int | None
    filing_url: str


class PlannedSaleOut(BaseModel):
    id: int
    accession: str
    filing_url: str
    filed_on: date | None
    seller: EntityLink
    issuer: EntityLink | None = Field(None, description="the company whose shares the notice is about "
                                                        "(given on a seller's profile)")
    ticker: str | None = Field(None, description="that company's ticker, when it has a ticker page")
    relationships: list[str]
    shares: Decimal | None
    market_value: Decimal | None
    approx_sale_on: date | None
    status: str
    matched_shares: Decimal | None
    plan_adoption_dates: list[date]


class PlannedSaleMatchOut(BaseModel):
    """A Form 4 sale linked to a Form 144 notice as its likely execution."""

    transaction_id: int
    traded_on: date | None
    shares: Decimal | None
    price: Decimal | None
    confidence: Decimal


class FootnoteOut(BaseModel):
    id: str
    text: str


class HoldingOut(BaseModel):
    """One holding listed on a Form 3 (the first statement a new insider files).

    For a derivative holding (an option, a restricted stock unit, a warrant), Form 3
    gives no count of the derivatives themselves: `shares` is then None and the
    size is `underlying_shares`, the number of shares it gives a right to. The
    terms (price, dates) are as filed; missing ones are None, never guessed.
    """

    security_title: str | None
    is_derivative: bool
    shares: Decimal | None
    ownership: str = Field(description="Direct, or the indirect holder text")
    underlying_title: str | None = Field(default=None, description="derivatives: the security it is on")
    underlying_shares: Decimal | None = Field(default=None, description="derivatives: shares it is on")
    exercise_price: Decimal | None = Field(default=None, description="derivatives: exercise or conversion price")
    exercise_date: date | None = Field(default=None, description="derivatives: first date it can be exercised")
    expiration_date: date | None = Field(default=None, description="derivatives: date it expires")
    footnote_ids: list[str] = Field(default_factory=list, description="the filing's footnotes on this line")


class FeedEventDetail(BaseModel):
    """The quick look for a feed event that isn't a single trade: a Form 144 notice or a Form 3."""

    item: FeedItem
    accession: str
    filing_url: str
    filed_on: date | None
    accepted_at: datetime | None
    planned_sale: PlannedSaleOut | None = None
    matches: list[PlannedSaleMatchOut] = Field(default_factory=list)
    holdings: list[HoldingOut] = Field(default_factory=list)
    holding_footnotes: list[FootnoteOut] = Field(
        default_factory=list, description="Form 3: the footnotes the holdings refer to, in order")


class TradeDetail(BaseModel):
    """Everything the quick-look drawer shows for one trade."""

    trade: TradeRow
    owners: list[EntityLink]
    footnotes: list[FootnoteOut]
    remarks: str | None
    raw_document_url: str | None
    superseded_by: int | None
    supersedes: int | None
    review_note: str | None
    source_note: str | None = Field(
        default=None, description="set when the row came from the SEC bulk data set (rounded values)")
    copies: list[TradeCopy] = Field(
        default_factory=list,
        description="other filings' current rows for this same trade that point at this one "
                    "(duplicate_of), directly or through another copy; totals count the trade once")


class SearchHit(BaseModel):
    kind: str = Field(description="person | fund | company | ticker")
    label: str
    sublabel: str | None = Field(description="ticker: the company; person or fund: role and company, "
                                             "e.g. 'Director, Snowflake Inc.'; company: its ticker")
    href: str


class SearchResults(BaseModel):
    query: str
    hits: list[SearchHit]


class PriceBarOut(BaseModel):
    """One daily close (split-adjusted, as the provider reports it)."""

    day: date
    close: Decimal


class PriceSeriesOut(BaseModel):
    """Daily closes for one ticker from one provider. Private mode only (PLAN.md decision 1)."""

    symbol: str
    provider: str = Field(description="where the prices came from, e.g. finlake or yfinance")
    licence: str = Field(description="personal-use-only | public-display-ok")
    bars: list[PriceBarOut]
