"""Trades and holdings reported by people: Forms 3/4/5 now, congressional PTRs later.

One unified `transactions` table holds every reported trade line. Key rules
(PLAN.md §5.4):

* `dedupe_key` is identical whichever path a filing arrives by (bulk SEC data
  set or live XML): source + filing key + table + 1-based XML row position.
  Loading the same filing twice never creates a second row.
* A joint filing (a fund, its GP and its manager) is ONE transaction with
  several `transaction_owners`. Aggregates count transactions, never owners.
* A missing number is NULL, never zero.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from smt.db.base import Base, BigIntPK, CreatedAt


class Transaction(Base):
    """One reported trade line."""

    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint("direction in ('buy','sell','exchange','other')", name="direction_valid"),
        CheckConstraint(
            "owner_type in ('self','spouse','joint','dependent','indirect')", name="owner_type_valid"
        ),
        CheckConstraint(
            "disclosed_precision in ('timestamp','date_only')", name="disclosed_precision_valid"
        ),
        Index("ix_transactions_entity_id_traded_on", "entity_id", "traded_on"),
        Index("ix_transactions_security_id_traded_on", "security_id", "traded_on"),
        Index("ix_transactions_issuer_entity_id_traded_on", "issuer_entity_id", "traded_on"),
        Index("ix_transactions_filing_id", "filing_id"),
        Index("ix_transactions_code_traded_on", "code", "traded_on"),
        Index("ix_transactions_detected_at_id", "detected_at", "id"),
        Index("ix_transactions_flagged", "id", postgresql_where=text("cardinality(data_issues) > 0")),
        Index("ix_transactions_duplicate_of_id", "duplicate_of_id", postgresql_where=text("duplicate_of_id IS NOT NULL")),
    )

    id: Mapped[BigIntPK]
    filing_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("filings.id", ondelete="CASCADE"))
    #: form4 | form5 | form3 | house_ptr | senate_ptr | oge278t
    source: Mapped[str] = mapped_column(String(20))
    #: nonderivative | derivative (Forms 3/4/5); ptr (congress)
    table_name: Mapped[str] = mapped_column(String(20))
    #: 1-based position of the row within its table in the primary document.
    line_no: Mapped[int] = mapped_column(Integer)
    dedupe_key: Mapped[str] = mapped_column(Text, unique=True)

    # ---- who ----
    #: The owner the trade is attributed to (counted once; PLAN.md decision 8).
    entity_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("entities.id", ondelete="CASCADE"))
    issuer_entity_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("entities.id", ondelete="SET NULL"))
    owner_type: Mapped[str] = mapped_column(String(12), server_default="self")
    #: D (direct) or I (indirect), as reported.
    ownership_nature: Mapped[str | None] = mapped_column(String(1))
    nature_text: Mapped[str | None] = mapped_column(Text)

    # ---- what ----
    security_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("securities.id", ondelete="SET NULL"))
    security_title: Mapped[str | None] = mapped_column(Text)
    raw_asset_text: Mapped[str | None] = mapped_column(Text)
    raw_ticker: Mapped[str | None] = mapped_column(String(20))
    match_method: Mapped[str | None] = mapped_column(String(20))
    match_confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    is_derivative: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    underlying_title: Mapped[str | None] = mapped_column(Text)
    underlying_shares: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    exercise_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    exercise_date: Mapped[date | None] = mapped_column(Date)
    expiration_date: Mapped[date | None] = mapped_column(Date)

    # ---- how ----
    direction: Mapped[str] = mapped_column(String(10))
    #: Form 4 transaction code (P, S, A, M, F, G, ...) or PTR type (P, S, S (partial), E).
    code: Mapped[str | None] = mapped_column(String(12))
    #: True only for open-market or private purchases/sales (codes P and S).
    is_open_market: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    #: Rule 10b5-1 plan box (Forms 4/5 since 2023). NULL when not reported.
    is_10b5_1: Mapped[bool | None] = mapped_column(Boolean)
    equity_swap: Mapped[bool | None] = mapped_column(Boolean)
    #: Same-day M (exercise) + S (sale) by the same owner share a group id.
    exercise_and_sell_group: Mapped[str | None] = mapped_column(String(40))

    # ---- how much ----
    shares: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    price: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    #: True when a footnote says the price is a weighted average of a range.
    price_is_weighted_avg: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    value: Mapped[Decimal | None] = mapped_column(Numeric(24, 2))
    value_low: Mapped[Decimal | None] = mapped_column(Numeric(24, 2))
    value_high: Mapped[Decimal | None] = mapped_column(Numeric(24, 2))
    value_is_bracket: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    value_high_open: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    #: A = acquired, D = disposed, as reported.
    acquired_disposed: Mapped[str | None] = mapped_column(String(1))
    shares_owned_after: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))

    # ---- when ----
    traded_on: Mapped[date | None] = mapped_column(Date)
    deemed_execution_on: Mapped[date | None] = mapped_column(Date)
    notified_on: Mapped[date | None] = mapped_column(Date)
    disclosed_at: Mapped[datetime | None]
    disclosed_precision: Mapped[str] = mapped_column(String(10), server_default="timestamp")
    detected_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    #: Calendar days from trade to disclosure (what the UI shows as "Filed N days later").
    delay_days: Mapped[int | None] = mapped_column(Integer)
    deadline_on: Mapped[date | None] = mapped_column(Date)
    deadline_rule: Mapped[str | None] = mapped_column(String(40))
    is_late: Mapped[bool | None] = mapped_column(Boolean)
    late_limb: Mapped[str | None] = mapped_column(String(20))
    business_days_late: Mapped[int | None] = mapped_column(Integer)
    #: The filer's own timeliness mark on Forms 4/5: E (early) or L (late).
    filer_timeliness: Mapped[str | None] = mapped_column(String(1))

    # ---- lineage ----
    supersedes_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("transactions.id", ondelete="SET NULL"))
    superseded_by_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("transactions.id", ondelete="SET NULL"))
    #: False once superseded by an amendment (or flagged "superseded, needs review").
    is_current: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    review_note: Mapped[str | None] = mapped_column(Text)
    #: Values that cannot be right as filed (smt.ingest.quality): "date_after_filing",
    #: "value_implausible". The numbers stay as filed; flagged figures are left out
    #: of rankings and totals.
    data_issues: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    #: The same trade reported in another filing (a fund and its general partner): points at
    #: the copy that counts in totals and rankings. Set by the quality job (smt.ingest.quality).
    duplicate_of_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("transactions.id", ondelete="SET NULL", name="fk_transactions_duplicate_of_id_transactions"))
    #: Footnote ids referenced anywhere in this row, e.g. ["F1", "F3"].
    footnote_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    comment: Mapped[str | None] = mapped_column(Text)
    parser_version: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[CreatedAt]


class TransactionOwner(Base):
    """Every reporting owner named on a (joint) filing, per transaction."""

    __tablename__ = "transaction_owners"
    __table_args__ = (
        PrimaryKeyConstraint("transaction_id", "entity_id"),
        Index("ix_transaction_owners_entity_id", "entity_id"),
    )

    transaction_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("transactions.id", ondelete="CASCADE"))
    entity_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("entities.id", ondelete="CASCADE"))
    is_primary: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))


class InsiderHolding(Base):
    """A holdings line (Form 3 snapshot, or holding rows on Forms 4/5).

    Form 3 reports what an insider owns on becoming one, not trades.
    """

    __tablename__ = "insider_holdings"
    __table_args__ = (
        Index("ix_insider_holdings_entity_id", "entity_id"),
        Index("ix_insider_holdings_issuer_entity_id", "issuer_entity_id"),
    )

    id: Mapped[BigIntPK]
    filing_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("filings.id", ondelete="CASCADE"))
    dedupe_key: Mapped[str] = mapped_column(Text, unique=True)
    table_name: Mapped[str] = mapped_column(String(20))
    line_no: Mapped[int] = mapped_column(Integer)
    entity_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("entities.id", ondelete="CASCADE"))
    issuer_entity_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("entities.id", ondelete="SET NULL"))
    security_title: Mapped[str | None] = mapped_column(Text)
    is_derivative: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    shares: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    ownership_nature: Mapped[str | None] = mapped_column(String(1))
    nature_text: Mapped[str | None] = mapped_column(Text)
    as_of: Mapped[date | None] = mapped_column(Date)
    footnote_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    # ---- derivative holdings only (Table II: options, restricted stock units, warrants) ----
    # Form 3's Table II has no column for how many derivatives are held, so `shares` is
    # usually empty there; the size is the number of underlying shares. Same types as
    # the matching Transaction columns. Filled from migration 0010 on (older rows fill
    # when their filing is loaded again).
    underlying_title: Mapped[str | None] = mapped_column(Text)
    underlying_shares: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    exercise_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    exercise_date: Mapped[date | None] = mapped_column(Date)
    expiration_date: Mapped[date | None] = mapped_column(Date)


class PlannedSale(Base):
    """A notice of a planned sale: Form 144 now; H.R. 7008 notices if enacted.

    A Form 144 is notice of an *intended* sale, not a completed one. The UI
    says "intends to sell ~$X" and, when found, links the Form 4 that executed it.
    """

    __tablename__ = "planned_sales"
    __table_args__ = (
        CheckConstraint("status in ('open','matched','no_form4_found','withdrawn')", name="status_valid"),
        Index("ix_planned_sales_seller_entity_id", "seller_entity_id"),
        Index("ix_planned_sales_issuer_entity_id", "issuer_entity_id"),
    )

    id: Mapped[BigIntPK]
    filing_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("filings.id", ondelete="CASCADE"), unique=True)
    #: form144 | hr7008_notice
    source: Mapped[str] = mapped_column(String(20))
    seller_entity_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("entities.id", ondelete="CASCADE"))
    issuer_entity_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("entities.id", ondelete="SET NULL"))
    security_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("securities.id", ondelete="SET NULL"))
    security_title: Mapped[str | None] = mapped_column(Text)
    relationships: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    shares: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    market_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 2))
    shares_outstanding: Mapped[Decimal | None] = mapped_column(Numeric(24, 2))
    approx_sale_on: Mapped[date | None] = mapped_column(Date)
    broker: Mapped[str | None] = mapped_column(Text)
    exchange: Mapped[str | None] = mapped_column(Text)
    plan_adoption_dates: Mapped[list[date]] = mapped_column(ARRAY(Date), server_default=text("'{}'"))
    details: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(String(20), server_default="open")
    matched_shares: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    match_confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    created_at: Mapped[CreatedAt]


class PlannedSaleMatch(Base):
    """A Form 4 sale row believed to execute a planned sale."""

    __tablename__ = "planned_sale_matches"
    __table_args__ = (PrimaryKeyConstraint("planned_sale_id", "transaction_id"),)

    planned_sale_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("planned_sales.id", ondelete="CASCADE"))
    transaction_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("transactions.id", ondelete="CASCADE"))
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    matched_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
