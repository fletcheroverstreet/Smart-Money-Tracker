"""Tradable securities and their prices.

A *security* is one tradable class (BRK.A and BRK.B are two securities of one
issuer). Tickers, CUSIPs and FIGIs live in `security_ids` with validity dates:
tickers get reused, so a 2014 trade in "FB" must resolve as of 2014.

Every stored price carries its provider and a `display_public` licence flag
(PLAN.md decision 1). Public API responses drop prices whose licence does not
allow public display.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    ForeignKey,
    Index,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from smt.db.base import Base, BigIntPK, CreatedAt


class Security(Base):
    """One tradable class of an issuer's securities."""

    __tablename__ = "securities"
    __table_args__ = (
        Index("ix_securities_issuer_entity_id", "issuer_entity_id"),
        Index("ix_securities_primary_ticker", "primary_ticker"),
        Index("ix_securities_name_trgm", "name", postgresql_using="gin",
              postgresql_ops={"name": "gin_trgm_ops"}),
    )

    id: Mapped[BigIntPK]
    issuer_entity_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("entities.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(Text)
    class_title: Mapped[str | None] = mapped_column(Text)
    #: common | preferred | etf | adr | fund | bond | option | other
    security_type: Mapped[str] = mapped_column(String(20), server_default="common")
    primary_ticker: Mapped[str | None] = mapped_column(String(20))
    exchange: Mapped[str | None] = mapped_column(String(20))
    sector: Mapped[str | None] = mapped_column(Text)
    industry: Mapped[str | None] = mapped_column(Text)
    #: curated (lodestar industry_map.csv) | sic | unmapped
    sector_source: Mapped[str] = mapped_column(String(20), server_default="unmapped")
    sic: Mapped[str | None] = mapped_column(String(4))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    delisted_on: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[CreatedAt]


class SecurityId(Base):
    """An identifier for a security over a date range (ticker, CUSIP, FIGI)."""

    __tablename__ = "security_ids"
    __table_args__ = (
        UniqueConstraint("scheme", "value", "valid_from", postgresql_nulls_not_distinct=True),
        Index("ix_security_ids_scheme_value", "scheme", "value"),
        Index("ix_security_ids_security_id", "security_id"),
    )

    id: Mapped[BigIntPK]
    security_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("securities.id", ondelete="CASCADE"))
    scheme: Mapped[str] = mapped_column(String(20))
    value: Mapped[str] = mapped_column(Text)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    source: Mapped[str] = mapped_column(Text)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))


class PriceDaily(Base):
    """One daily bar from one provider.

    `close` is split-adjusted as delivered; `adj_close` also reinvests
    dividends and is what return calculations use.
    """

    __tablename__ = "prices_daily"
    __table_args__ = (PrimaryKeyConstraint("security_id", "day", "provider"),)

    security_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("securities.id", ondelete="CASCADE"))
    day: Mapped[date] = mapped_column(Date)
    provider: Mapped[str] = mapped_column(String(20))
    open: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    high: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    low: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    close: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    adj_close: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    volume: Mapped[int | None] = mapped_column(BigInteger)
    #: Licence flag: may this bar be shown to the public? (PLAN.md decision 1)
    display_public: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    fetched_at: Mapped[datetime] = mapped_column(server_default=text("now()"))


class PriceSplit(Base):
    """A stock split as one provider records it: from `ex_date` on, one old share is `ratio` new
    ones (2 = a 2-for-1 split, 0.1 = a 1-for-10 reverse split).

    Providers apply splits to every earlier close when they deliver a series, so a close stored
    after a split no longer shows the price that day's trades were made at. The quality checks
    use these to know which stored closes are as traded (smt.ingest.quality.flag_totals_as_price).
    """

    __tablename__ = "price_splits"
    __table_args__ = (PrimaryKeyConstraint("security_id", "provider", "ex_date"),)

    security_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("securities.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(20))
    ex_date: Mapped[date] = mapped_column(Date)
    ratio: Mapped[Decimal] = mapped_column(Numeric(20, 10))


class PriceSplitCheck(Base):
    """When one provider's split list for a security was last read. Every split that provider
    could have applied to a close fetched before `checked_at` is in price_splits."""

    __tablename__ = "price_split_checks"
    __table_args__ = (PrimaryKeyConstraint("security_id", "provider"),)

    security_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("securities.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(20))
    checked_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
