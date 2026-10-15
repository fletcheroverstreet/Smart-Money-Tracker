"""Who is who: people, companies, funds, and the identifiers that link them.

An *entity* is anything that can appear in a disclosure: an insider, a member
of Congress, a company, a fund manager. The same real-world entity is known by
many identifiers (SEC CIK, bioguide ID, Wikidata QID, ...). Those live in
`entity_ids` with validity dates, because identifiers change over time: a fund
can move its 13F filing to a new CIK, and a ticker can be reused.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from smt.db.base import Base, BigIntPK, CreatedAt

ENTITY_KINDS = ("person", "politician", "company", "fund", "bank", "index_manager")


class Entity(Base):
    """A person or organization that files, or is named in, a disclosure."""

    __tablename__ = "entities"
    __table_args__ = (
        CheckConstraint(
            "kind in ('person','politician','company','fund','bank','index_manager')",
            name="kind_valid",
        ),
        Index("ix_entities_name_trgm", "name", postgresql_using="gin",
              postgresql_ops={"name": "gin_trgm_ops"}),
    )

    id: Mapped[BigIntPK]
    kind: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(Text)
    #: "Last, First" for people, so directories sort the way readers expect.
    sort_name: Mapped[str] = mapped_column(Text)
    slug: Mapped[str] = mapped_column(Text, unique=True)
    #: Free-form flags such as 'insider', 'tracked', 'activist', 'ten_pct_owner'.
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    summary: Mapped[str | None] = mapped_column(Text)
    summary_source: Mapped[str | None] = mapped_column(String(20))
    photo_url: Mapped[str | None] = mapped_column(Text)
    photo_credit: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[CreatedAt]


class EntityId(Base):
    """An external identifier for an entity, valid over a date range.

    Examples: ("sec_cik", "0001067983"), ("bioguide", "P000197").
    `valid_to` is NULL while the identifier is current.
    """

    __tablename__ = "entity_ids"
    __table_args__ = (
        UniqueConstraint("scheme", "value"),
        Index("ix_entity_ids_entity_id", "entity_id"),
    )

    id: Mapped[BigIntPK]
    entity_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("entities.id", ondelete="CASCADE"))
    scheme: Mapped[str] = mapped_column(String(30))
    value: Mapped[str] = mapped_column(Text)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    source: Mapped[str] = mapped_column(Text)


class EntityLink(Base):
    """A typed relationship between two entities.

    `reporting_alias_of` is how a fund's Form 4 filer (often a GP or a master
    fund with its own CIK) is linked to the manager whose 13F we track, so a
    10%-owner trade shows up on the Institution profile.
    """

    __tablename__ = "entity_links"
    __table_args__ = (UniqueConstraint("from_id", "to_id", "link_type"),)

    id: Mapped[BigIntPK]
    from_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("entities.id", ondelete="CASCADE"))
    to_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("entities.id", ondelete="CASCADE"))
    link_type: Mapped[str] = mapped_column(String(30))
    source: Mapped[str] = mapped_column(Text)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    note: Mapped[str | None] = mapped_column(Text)


class Role(Base):
    """A dated role: officer/director/10% owner of a company, senator, committee seat.

    Insider roles come from the `reportingOwnerRelationship` block of Forms
    3/4/5. The first Form 3 marks when someone became an insider (tenure).
    """

    __tablename__ = "roles"
    __table_args__ = (
        # One row per (person, organization, role, title). NULLS NOT DISTINCT so a
        # role with no organization can't be duplicated on every reload.
        UniqueConstraint("entity_id", "org_entity_id", "role_type", "title",
                         postgresql_nulls_not_distinct=True),
        Index("ix_roles_org_entity_id", "org_entity_id"),
    )

    id: Mapped[BigIntPK]
    entity_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("entities.id", ondelete="CASCADE"))
    org_entity_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("entities.id", ondelete="CASCADE"))
    #: officer | director | ten_pct_owner | other_insider | senator | representative | committee
    role_type: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(Text, server_default="")
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    last_seen_on: Mapped[date | None] = mapped_column(Date)
    source_filing_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("filings.id", ondelete="SET NULL"))
    source: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
