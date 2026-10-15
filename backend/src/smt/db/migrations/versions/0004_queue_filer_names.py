"""Queue keeps EDGAR's conformed filer names; flag people named from Form 144 free text.

Form 144 notices give the seller's name as free text whose word order depends
on the filing agent ("Timothy Cutt" in one notice, "Dageville Benoit" in
another). People first seen on a Form 144 were therefore sometimes named
backwards. From now on the loader names them from EDGAR's conformed name
(always "LAST FIRST MIDDLE"), carried in `edgar_queue.filer_names`.

Existing people who were first seen on a Form 144 get the `name_unverified`
tag, so the `edgar.verify_names` job re-derives their names from a Form 4 they
filed or from their SEC submissions record.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "edgar_queue",
        sa.Column("filer_names", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"),
                  nullable=False),
    )
    op.execute(
        "UPDATE entities SET tags = array_append(tags, 'name_unverified') "
        "WHERE kind = 'person' AND 'form144_filer' = ANY(tags) AND NOT 'name_unverified' = ANY(tags)"
    )


def downgrade() -> None:
    op.execute("UPDATE entities SET tags = array_remove(tags, 'name_unverified')")
    op.drop_column("edgar_queue", "filer_names")
