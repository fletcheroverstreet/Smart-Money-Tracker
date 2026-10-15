"""transactions.data_issues: flags for values that cannot be right as filed.

See smt.ingest.quality. Rows keep their numbers as filed; the flags keep a
typo (a 2029 trade date, a total typed as the price) out of rankings and totals.
Existing rows are flagged by the quality.flag_transactions job.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column("data_issues", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False),
    )
    # Flagged rows are few; a partial index keeps "exclude flagged" filters cheap.
    op.create_index("ix_transactions_flagged", "transactions", ["id"],
                    postgresql_where=sa.text("cardinality(data_issues) > 0"))


def downgrade() -> None:
    op.drop_index("ix_transactions_flagged", table_name="transactions")
    op.drop_column("transactions", "data_issues")
