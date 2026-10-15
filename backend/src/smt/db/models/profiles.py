"""Company profiles from the SEC's submissions files.

`https://data.sec.gov/submissions/CIK##########.json` describes one filer: its
current name, former names, SIC industry code, tickers and exchanges. We keep
the parts the site uses. The file itself is stored raw first, like every
other fetched document.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Date, ForeignKey, Integer, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from smt.db.base import Base


class IssuerProfile(Base):
    """What the SEC's submissions file says about one company (one row per issuer).

    The SIC code is the sector fallback for securities that aren't in the
    curated sector map (lodestar's industry_map.csv). `checked_at` is the last
    time we asked the SEC; `updated_at` is the last time the answer changed.
    """

    __tablename__ = "issuer_profiles"

    entity_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True)
    #: Ten-digit, zero-padded SEC CIK the profile was fetched for.
    cik: Mapped[str] = mapped_column(String(10), unique=True)
    #: HTTP status of the last check: 200, or 404 when the SEC has no file for this CIK.
    http_status: Mapped[int] = mapped_column(Integer)
    #: The company name exactly as the SEC has it today.
    sec_name: Mapped[str | None] = mapped_column(Text)
    #: The SEC's entity type, e.g. "operating", "other".
    entity_type: Mapped[str | None] = mapped_column(String(40))
    #: Standard Industrial Classification code (four digits) and its SEC description.
    sic: Mapped[str | None] = mapped_column(String(4))
    sic_description: Mapped[str | None] = mapped_column(Text)
    #: Current tickers and their exchanges, in the SEC's order (parallel lists).
    tickers: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    exchanges: Mapped[list[str | None]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    #: Earlier names with their date ranges: [{"name": ..., "from": "2021-02-17", "to": "2023-02-10"}].
    former_names: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    state_of_incorporation: Mapped[str | None] = mapped_column(String(10))
    #: Fiscal year end as MMDD, e.g. "1231".
    fiscal_year_end: Mapped[str | None] = mapped_column(String(4))
    #: The stored raw file this profile was parsed from.
    raw_document_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("raw_documents.id", ondelete="SET NULL"))
    #: SHA-256 of that file, so an unchanged file is not re-applied.
    sha256: Mapped[str | None] = mapped_column(String(64))
    parser_version: Mapped[str | None] = mapped_column(String(20))
    checked_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(server_default=text("now()"))


class IssuerPublicFloat(Base):
    """A company's latest reported public float, from the SEC's XBRL data (dei:EntityPublicFloat).

    The public float is the market value of the shares not held by insiders, as the company
    states it on the cover of its annual report (as of the end of its second fiscal quarter).
    It is an outside yardstick for a trade's size: no single insider trade is many times the
    value of the whole float. Fetched only for companies with large trades (smt.sources.edgar
    .public_float); `public_float` is NULL when the SEC has none (foreign filers, new listings).
    """

    __tablename__ = "issuer_public_float"

    entity_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True)
    cik: Mapped[str] = mapped_column(String(10))
    #: HTTP status of the last check: 200, or 404 when the SEC has no such figure for the company.
    http_status: Mapped[int] = mapped_column(Integer)
    #: US dollars, and the date the float was measured at.
    public_float: Mapped[Decimal | None] = mapped_column(Numeric(24, 2))
    as_of: Mapped[date | None] = mapped_column(Date)
    raw_document_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("raw_documents.id", ondelete="SET NULL"))
    checked_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
