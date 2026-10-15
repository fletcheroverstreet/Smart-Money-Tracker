"""The terms of derivative holdings (options, restricted stock units, warrants).

A Form 3 lists what a new insider already owns. Its Table II (derivatives) has
no column for how many options are held: the size is the number of underlying
shares, and the rows differ by exercise price and dates. insider_holdings kept
none of these, so two option grants showed as identical lines with no size.
The columns match the ones transactions already has.

Existing rows stay empty until their filing is loaded again (`smt reprocess`
for filings loaded from their XML; `smt backfill --force` for the quarters
loaded from the SEC data sets).

Revision ID: 0010
Revises: 0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("insider_holdings", sa.Column("underlying_title", sa.Text(), nullable=True))
    op.add_column("insider_holdings", sa.Column("underlying_shares", sa.Numeric(24, 6), nullable=True))
    op.add_column("insider_holdings", sa.Column("exercise_price", sa.Numeric(24, 6), nullable=True))
    op.add_column("insider_holdings", sa.Column("exercise_date", sa.Date(), nullable=True))
    op.add_column("insider_holdings", sa.Column("expiration_date", sa.Date(), nullable=True))


def downgrade() -> None:
    for column in ("expiration_date", "exercise_date", "exercise_price", "underlying_shares", "underlying_title"):
        op.drop_column("insider_holdings", column)
