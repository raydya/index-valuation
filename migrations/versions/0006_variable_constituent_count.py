"""Allow broad-market indices without a fixed constituent count.

Revision ID: 0006
Revises: ec157f69d574
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "ec157f69d574"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("indices", "expected_constituents", existing_type=sa.Integer(), nullable=True)


def downgrade():
    # Fails explicitly if variable-count indices still exist; do not invent a count.
    op.alter_column("indices", "expected_constituents", existing_type=sa.Integer(), nullable=False)
