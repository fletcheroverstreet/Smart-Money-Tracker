"""A partial index for the Live feed, and precomputed per-person trade stats for the directory.

* feed_events holds every event, history included (250k+ rows), but the Live
  feed reads only is_live rows newest first. Without an index for that, the
  default feed took ~1.1 s scanning history; this makes it a short index read.
* entity_trade_stats: per person (reporting owner), the counts the People
  directory sorts by. Aggregating every trade on each request took ~1.1 s. The
  view is refreshed every few minutes by the people.refresh_stats job, so the
  directory can lag new filings by that much; profiles still compute live.
  Its counting rule must stay the same as smt.api.queries.PERSON_COUNTED (a
  change there needs a new migration that recreates this view).

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS ix_feed_events_live ON feed_events (detected_at DESC, id DESC) "
               "WHERE is_live")
    op.execute("""
        CREATE MATERIALIZED VIEW entity_trade_stats AS
        SELECT o.entity_id,
               count(*) AS trades,
               count(*) FILTER (WHERE t.code = 'P' AND c.counted) AS buys,
               count(*) FILTER (WHERE t.code = 'S' AND c.counted) AS sells,
               -- a trade date after its own filing is a typo; it can't be the "last trade"
               max(t.traded_on) FILTER (WHERE NOT ('date_after_filing' = ANY(t.data_issues))) AS last_traded_on
          FROM transaction_owners o
          JOIN transactions t ON t.id = o.transaction_id AND t.is_current
          CROSS JOIN LATERAL (SELECT
                t.is_open_market AND NOT ('amendment_unpaired' = ANY(t.data_issues))
                AND (t.duplicate_of_id IS NULL OR NOT EXISTS (
                    SELECT 1 FROM transaction_owners o2
                     WHERE o2.transaction_id = t.duplicate_of_id AND o2.entity_id = o.entity_id)) AS counted) c
         GROUP BY o.entity_id
    """)
    op.execute("CREATE UNIQUE INDEX ix_entity_trade_stats_entity ON entity_trade_stats (entity_id)")
    op.execute("CREATE INDEX ix_entity_trade_stats_recent ON entity_trade_stats (last_traded_on DESC NULLS LAST)")


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS entity_trade_stats")
    op.execute("DROP INDEX IF EXISTS ix_feed_events_live")
