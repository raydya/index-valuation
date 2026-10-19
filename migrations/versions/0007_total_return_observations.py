"""Store dividend-reinvestment levels independently of index valuations.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "total_return_observations",
        sa.Column("index_key", sa.String(64), sa.ForeignKey("indices.key"), primary_key=True),
        sa.Column("index_code", sa.String(32), primary_key=True),
        sa.Column("source", sa.String(32), primary_key=True),
        sa.Column("symbol", sa.String(32), primary_key=True),
        sa.Column("basis", sa.String(32), primary_key=True),
        sa.Column("trade_date", sa.Date(), primary_key=True),
        sa.Column("price", sa.Numeric(30, 16), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("quote_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("artifact_path", sa.String(), nullable=False),
        sa.Column("raw", postgresql.JSONB(), nullable=False),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("pipeline_runs.id"), nullable=False),
        sa.CheckConstraint("price > 0", name="positive_return_level"),
    )


def downgrade():
    op.drop_table("total_return_observations")
