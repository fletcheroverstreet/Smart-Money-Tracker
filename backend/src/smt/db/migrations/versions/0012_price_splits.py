"""Stock splits as each price provider records them, and when they were read, so the value
checks can tell which stored closes are still as traded (smt.ingest.quality.flag_totals_as_price).

Revision ID: 0012
Revises: 0011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "price_splits",
        sa.Column("security_id", sa.BigInteger(), sa.ForeignKey("securities.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("ex_date", sa.Date(), nullable=False),
        sa.Column("ratio", sa.Numeric(20, 10), nullable=False),
        sa.PrimaryKeyConstraint("security_id", "provider", "ex_date"),
    )
    op.create_table(
        "price_split_checks",
        sa.Column("security_id", sa.BigInteger(), sa.ForeignKey("securities.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("security_id", "provider"),
    )


def downgrade() -> None:
    op.drop_table("price_split_checks")
    op.drop_table("price_splits")
