"""transactions.duplicate_of_id; option rows are no longer "open market".

* duplicate_of_id: the same trade reported in more than one filing (a fund
  and its general partner, or split filer groups) points at the copy that
  counts. Totals and rankings count each trade once; every owner's page still
  lists it. Filled by the quality.flag_transactions job (smt.ingest.quality).
* is_open_market was true for every P/S row, including Table II rows, which
  are option contracts bought or written, not buying or selling the stock.
  Those rows now carry is_open_market = false.

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("duplicate_of_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key("fk_transactions_duplicate_of_id_transactions", "transactions", "transactions",
                          ["duplicate_of_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_transactions_duplicate_of_id", "transactions", ["duplicate_of_id"],
                    postgresql_where=sa.text("duplicate_of_id IS NOT NULL"))
    op.execute("UPDATE transactions SET is_open_market = false WHERE is_derivative AND is_open_market")
    op.execute("UPDATE feed_events SET payload = jsonb_set(payload, '{open_market}', 'false') "
               "WHERE payload->>'derivative' = 'true' AND payload->>'open_market' = 'true'")


def downgrade() -> None:
    op.execute("UPDATE transactions SET is_open_market = true WHERE is_derivative AND code IN ('P', 'S')")
    op.drop_index("ix_transactions_duplicate_of_id", table_name="transactions")
    op.drop_constraint("fk_transactions_duplicate_of_id_transactions", "transactions", type_="foreignkey")
    op.drop_column("transactions", "duplicate_of_id")
