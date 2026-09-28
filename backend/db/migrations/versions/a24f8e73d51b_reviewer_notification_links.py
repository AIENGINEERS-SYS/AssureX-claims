"""Route reviewer notifications to the reviewer workspace.

Revision ID: a24f8e73d51b
Revises: f24d4c91a6e2
"""
from alembic import op
import sqlalchemy as sa

revision = "a24f8e73d51b"
down_revision = "f24d4c91a6e2"
branch_labels = depends_on = None


def upgrade():
    op.execute(sa.text(
        "UPDATE notifications SET reference_type = 'review_claim' "
        "WHERE type = 'CLAIM_IN_REVIEW' "
        "AND user_id IN (SELECT id FROM users WHERE role = 'reviewer')"
    ))


def downgrade():
    op.execute(sa.text(
        "UPDATE notifications SET reference_type = 'claim' "
        "WHERE reference_type = 'review_claim'"
    ))
