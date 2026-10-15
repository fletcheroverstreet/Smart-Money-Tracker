"""Operations: the live feed log, job scheduling, runs, quarantine, reconciliation."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from smt.db.base import Base, BigIntPK, CreatedAt


class FeedEvent(Base):
    """One item in the Live feed, written in the same transaction as its source rows.

    It doubles as the SSE replay log. `is_live` is false for rows written by
    backfill, reprocess or reconciliation, so those never flood the feed.
    `payload` holds display-ready fields so the feed endpoint needs no joins.
    """

    __tablename__ = "feed_events"
    __table_args__ = (
        Index("ix_feed_events_detected_at_id", "detected_at", "id"),
        Index("ix_feed_events_security_id", "security_id"),
        Index("ix_feed_events_entity_id", "entity_id"),
        UniqueConstraint("event_key"),
    )

    id: Mapped[BigIntPK]
    #: Stable identity so re-ingesting never duplicates a feed item.
    event_key: Mapped[str] = mapped_column(Text)
    #: insider_trade | planned_sale | insider_holding | filing
    event_type: Mapped[str] = mapped_column(String(30))
    source: Mapped[str] = mapped_column(String(20))
    form_type: Mapped[str] = mapped_column(String(30))
    #: people | institutions
    section: Mapped[str] = mapped_column(String(20))
    filing_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("filings.id", ondelete="CASCADE"))
    transaction_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("transactions.id", ondelete="CASCADE"))
    entity_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("entities.id", ondelete="SET NULL"))
    security_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("securities.id", ondelete="SET NULL"))
    #: Size in dollars used for "size" filters (value, or bracket midpoint for PTRs).
    size_usd: Mapped[float | None]
    occurred_on: Mapped[date | None] = mapped_column(Date)
    disclosed_at: Mapped[datetime | None]
    detected_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    is_live: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    payload: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))


class IngestionRun(Base):
    """One run of one job: what it fetched, what it wrote, what failed."""

    __tablename__ = "ingestion_runs"
    __table_args__ = (Index("ix_ingestion_runs_job_started_at", "job", "started_at"),)

    id: Mapped[BigIntPK]
    job: Mapped[str] = mapped_column(String(60))
    source: Mapped[str] = mapped_column(String(20))
    started_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    finished_at: Mapped[datetime | None]
    #: running | ok | partial | failed
    status: Mapped[str] = mapped_column(String(12), server_default="running")
    docs_fetched: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    rows_new: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    rows_updated: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    parse_failures: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    #: Worst detected_at - accepted_at seen in this run, in seconds.
    max_lag_seconds: Mapped[float | None]
    error: Mapped[str | None] = mapped_column(Text)
    stats: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))


class SchedulerJob(Base):
    """Scheduler state per job: when it runs next, who holds the lease, its cursor.

    Leases make it impossible for two worker processes to run the same job at
    once; the cursor lets long backfills resume after a restart.
    """

    __tablename__ = "scheduler_jobs"

    name: Mapped[str] = mapped_column(String(60), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    next_run_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    lease_until: Mapped[datetime | None]
    leased_by: Mapped[str | None] = mapped_column(Text)
    last_run_id: Mapped[int | None] = mapped_column(BigInteger)
    last_ok_at: Mapped[datetime | None]
    cursor: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    updated_at: Mapped[CreatedAt]


class Quarantine(Base):
    """A document or row that failed validation. Never silently dropped (spec §11)."""

    __tablename__ = "quarantine"
    __table_args__ = (Index("ix_quarantine_source_created_at", "source", "created_at"),)

    id: Mapped[BigIntPK]
    source: Mapped[str] = mapped_column(String(30))
    raw_document_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("raw_documents.id", ondelete="SET NULL"))
    filing_ref: Mapped[str | None] = mapped_column(Text)
    row_ref: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    created_at: Mapped[CreatedAt]
    resolved_at: Mapped[datetime | None]
    resolution: Mapped[str | None] = mapped_column(Text)


class ReconciliationResult(Base):
    """Daily comparison of what the poller saw against an official index."""

    __tablename__ = "reconciliation_results"
    __table_args__ = (UniqueConstraint("source", "day"),)

    id: Mapped[BigIntPK]
    source: Mapped[str] = mapped_column(String(30))
    day: Mapped[date] = mapped_column(Date)
    expected: Mapped[int] = mapped_column(Integer)
    found: Mapped[int] = mapped_column(Integer)
    missing: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    explained: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    created_at: Mapped[CreatedAt]
    #: The latest check (created_at is the first).
    checked_at: Mapped[CreatedAt]


class EdgarQueue(Base):
    """Filings discovered but not yet fetched and loaded: a durable to-do list.

    The poller and the daily-index reconciliation both add accessions here; the
    processor claims them. Because the queue lives in the database, a restart
    never loses a discovered filing, and failures are retried with a limit.
    """

    __tablename__ = "edgar_queue"
    __table_args__ = (Index("ix_edgar_queue_status_priority", "status", "priority", "discovered_at"),)

    accession: Mapped[str] = mapped_column(String(25), primary_key=True)
    form_type: Mapped[str] = mapped_column(String(30))
    #: A CIK whose EDGAR directory holds the filing (any party's CIK works).
    cik: Mapped[str] = mapped_column(String(10))
    accepted_at: Mapped[datetime | None]
    filed_on: Mapped[date | None] = mapped_column(Date)
    #: feed | daily_index | backfill | manual
    discovered_via: Mapped[str] = mapped_column(String(20))
    #: {cik: EDGAR conformed name} for the filing's parties, from the feed or daily index.
    #: Form 144 notices spell the seller's name freely ("Timothy Cutt" or "Cutt Timothy"),
    #: so the loader names people from these instead.
    filer_names: Mapped[dict[str, str]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    discovered_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    priority: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    #: pending | done | failed | skipped
    status: Mapped[str] = mapped_column(String(12), server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    next_attempt_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    last_error: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[datetime | None]
