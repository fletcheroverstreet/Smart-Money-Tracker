"""Fetched documents and the filings they belong to.

Raw first, parse second: every fetched document is stored (compressed, named
by its SHA-256) and recorded in `raw_documents` *before* any parsing. Parsers
carry a version, so `smt reprocess` can rebuild every derived row from raw
after a parser improves or a format changes.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from smt.db.base import Base, BigIntPK, CreatedAt

PARSE_STATUSES = ("pending", "parsed", "failed", "quarantined", "not_machine_readable", "skipped")


class RawDocument(Base):
    """One fetched document, exactly as received."""

    __tablename__ = "raw_documents"
    __table_args__ = (
        UniqueConstraint("url", "sha256"),
        CheckConstraint(
            "parse_status in ('pending','parsed','failed','quarantined','not_machine_readable','skipped')",
            name="parse_status_valid",
        ),
        Index("ix_raw_documents_filing_id", "filing_id"),
        Index("ix_raw_documents_parse_status", "parse_status"),
    )

    id: Mapped[BigIntPK]
    source: Mapped[str] = mapped_column(String(30))
    url: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    fetched_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    http_status: Mapped[int] = mapped_column(Integer)
    etag: Mapped[str | None] = mapped_column(Text)
    last_modified: Mapped[str | None] = mapped_column(Text)
    content_type: Mapped[str | None] = mapped_column(Text)
    byte_size: Mapped[int] = mapped_column(BigInteger)
    #: Path relative to RAW_STORE_DIR, e.g. "ab/cd/abcd...zst".
    storage_path: Mapped[str] = mapped_column(Text)
    #: What this document is inside its filing: primary | information_table | index | ...
    doc_role: Mapped[str | None] = mapped_column(String(30))
    parse_status: Mapped[str] = mapped_column(String(24), server_default="pending")
    parser_name: Mapped[str | None] = mapped_column(String(40))
    parser_version: Mapped[str | None] = mapped_column(String(20))
    parse_error: Mapped[str | None] = mapped_column(Text)
    filing_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("filings.id", ondelete="SET NULL"))


class Filing(Base):
    """One disclosure filing: an EDGAR accession, a House DocID, a Senate report id.

    Two times are kept and never derived from each other:

    * `accepted_at`: when the regulator accepted it (EDGAR acceptance time, UTC);
    * `filed_on`: the legal filing date, which follows per-form cutoffs
      (Forms 3/4/5, 144 and 13D/13G accepted until 22:00 ET count as filed that
      day; other forms cut off at 17:30 ET; Reg S-T Rule 13).
    """

    __tablename__ = "filings"
    __table_args__ = (
        UniqueConstraint("source", "source_key"),
        Index("ix_filings_form_type_filed_on", "form_type", "filed_on"),
        Index("ix_filings_accepted_at", "accepted_at"),
        Index("ix_filings_issuer_entity_id", "issuer_entity_id"),
    )

    id: Mapped[BigIntPK]
    #: edgar | house | senate
    source: Mapped[str] = mapped_column(String(20))
    #: EDGAR accession number (0001193125-26-396718), House DocID, Senate UUID.
    source_key: Mapped[str] = mapped_column(String(64))
    form_type: Mapped[str] = mapped_column(String(30))
    accepted_at: Mapped[datetime | None]
    filed_on: Mapped[date | None] = mapped_column(Date)
    period_of_report: Mapped[date | None] = mapped_column(Date)
    #: The company the filing is about (Forms 3/4/5/144: the issuer).
    issuer_entity_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("entities.id", ondelete="SET NULL"))
    #: Human-readable index page on the regulator's site.
    url: Mapped[str] = mapped_column(Text)
    primary_doc_url: Mapped[str | None] = mapped_column(Text)
    schema_version: Mapped[str | None] = mapped_column(String(10))
    is_amendment: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    #: For a 4/A: the date of the original filing it amends (the only link EDGAR gives).
    original_filed_on: Mapped[date | None] = mapped_column(Date)
    amends_filing_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("filings.id", ondelete="SET NULL"))
    amendment_match: Mapped[str | None] = mapped_column(String(40))
    superseded_by_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("filings.id", ondelete="SET NULL"))
    machine_readable: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    #: Verbatim footnotes {"F1": "text", ...} and remarks, kept visible (spec §2.2).
    footnotes: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    remarks: Mapped[str | None] = mapped_column(Text)
    #: Filing-level flags, e.g. aff10b5One for Forms 4/5 (Rule 10b5-1 box).
    flags: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    #: How we found it: feed | daily_index | backfill | manual
    detected_via: Mapped[str] = mapped_column(String(20))
    #: When WE first saw it. detected_at - accepted_at is our pipeline lag.
    detected_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    parser_version: Mapped[str | None] = mapped_column(String(20))
    updated_at: Mapped[CreatedAt]


class FilingParty(Base):
    """An entity named on a filing and in which capacity."""

    __tablename__ = "filing_parties"
    __table_args__ = (
        PrimaryKeyConstraint("filing_id", "entity_id", "role"),
        Index("ix_filing_parties_entity_id", "entity_id"),
    )

    filing_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("filings.id", ondelete="CASCADE"))
    entity_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("entities.id", ondelete="CASCADE"))
    #: reporting_owner | issuer | filer | subject_company | filed_by
    role: Mapped[str] = mapped_column(String(20))
    is_primary: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    #: Relationship to the issuer as stated on this filing (Forms 3/4/5/144).
    relationship: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    position: Mapped[int] = mapped_column(Integer, server_default=text("0"))
