"""When each day was last checked against its daily index.

The gap filler re-checks a day that is still short of filings only after a
while (a filing that fails for good must not trigger a fetch every run), so
it needs the time of the last check; created_at keeps the first.

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("reconciliation_results",
                  sa.Column("checked_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False))
    op.execute("UPDATE reconciliation_results SET checked_at = created_at")


def downgrade() -> None:
    op.drop_column("reconciliation_results", "checked_at")
