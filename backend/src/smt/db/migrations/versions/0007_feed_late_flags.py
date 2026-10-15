"""Feed events carry the trade's late-filing flag; company names lose EDGAR's "/NEW/"-style tags.

* is_late and deadline_rule go into each insider-trade event's payload for the
  Live table. New events get them from the loader; this copies them onto older ones.
* Organization names created before the name tidy-up kept EDGAR suffixes such as
  "ONEOK INC /NEW/" or "/DE/"; they are removed (smt.domain.names.org_display).

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        UPDATE feed_events e
           SET payload = e.payload || jsonb_build_object('is_late', t.is_late, 'deadline_rule', t.deadline_rule)
          FROM transactions t
         WHERE e.transaction_id = t.id AND NOT (e.payload ? 'is_late')
    """)
    op.execute(r"""
        UPDATE entities
           SET name = regexp_replace(name, '\s*/[A-Z]{2,5}/?$', ''),
               sort_name = regexp_replace(sort_name, '\s*/[A-Z]{2,5}/?$', '')
         WHERE kind <> 'person' AND name ~ '/[A-Z]{2,5}/?$'
    """)


def downgrade() -> None:
    op.execute("UPDATE feed_events SET payload = payload - 'is_late' - 'deadline_rule'")
