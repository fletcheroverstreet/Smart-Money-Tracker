"""Companies' latest reported public float (dei:EntityPublicFloat), an outside yardstick for
trade sizes (smt.sources.edgar.public_float, smt.ingest.quality.flag_totals_as_price).

Revision ID: 0011
Revises: 0010
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "issuer_public_float",
        sa.Column("entity_id", sa.BigInteger(), sa.ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("cik", sa.String(10), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=False),
        sa.Column("public_float", sa.Numeric(24, 2), nullable=True),
        sa.Column("as_of", sa.Date(), nullable=True),
        sa.Column("raw_document_id", sa.BigInteger(),
                  sa.ForeignKey("raw_documents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("issuer_public_float")
