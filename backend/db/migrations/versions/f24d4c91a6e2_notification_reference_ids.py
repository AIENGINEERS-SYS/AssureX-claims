"""Harden Phase 24 notification references and analytics indexes.

Revision ID: f24d4c91a6e2
Revises: e24a9f4b7c10
"""
from alembic import op
import sqlalchemy as sa

revision = "f24d4c91a6e2"
down_revision = "e24a9f4b7c10"
branch_labels = depends_on = None


def upgrade():
    # Phase 24 initially backfilled internal integer foreign keys into
    # reference_id. Replace them with the public IDs users actually search for.
    op.execute(sa.text(
        "UPDATE notifications SET "
        "reference_type = 'claim', "
        "reference_id = (SELECT claims.claim_id FROM claims "
        "WHERE claims.id = notifications.claim_id) "
        "WHERE claim_id IS NOT NULL"
    ))
    op.execute(sa.text(
        "UPDATE notifications SET "
        "reference_type = 'product', "
        "reference_id = (SELECT products.product_id FROM products "
        "WHERE products.id = notifications.product_id) "
        "WHERE claim_id IS NULL AND product_id IS NOT NULL"
    ))

    op.create_index("ix_notifications_created_at", "notifications", ["created_at"])
    op.create_index(
        "ix_notifications_type_created",
        "notifications",
        ["type", "created_at"],
    )
    op.create_index(
        "ix_notifications_priority_read_created",
        "notifications",
        ["priority", "is_read", "created_at"],
    )


def downgrade():
    op.drop_index("ix_notifications_priority_read_created", table_name="notifications")
    op.drop_index("ix_notifications_type_created", table_name="notifications")
    op.drop_index("ix_notifications_created_at", table_name="notifications")

    # Restore the representation introduced by the previous migration.
    op.execute(sa.text(
        "UPDATE notifications SET "
        "reference_type = 'claim', "
        "reference_id = CAST(claim_id AS VARCHAR(80)) "
        "WHERE claim_id IS NOT NULL"
    ))
    op.execute(sa.text(
        "UPDATE notifications SET "
        "reference_type = 'product', "
        "reference_id = CAST(product_id AS VARCHAR(80)) "
        "WHERE claim_id IS NULL AND product_id IS NOT NULL"
    ))
