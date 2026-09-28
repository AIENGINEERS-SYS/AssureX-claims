"""Phase 24 notification center, preferences, priorities and filtering indexes.

Revision ID: e24a9f4b7c10
Revises: f23d7c9a102b
"""
from alembic import op
import sqlalchemy as sa

revision = "e24a9f4b7c10"
down_revision = "f23d7c9a102b"
branch_labels = depends_on = None


def upgrade():
    with op.batch_alter_table("notifications") as batch:
        batch.add_column(sa.Column("reference_type", sa.String(30), nullable=True))
        batch.add_column(sa.Column("reference_id", sa.String(80), nullable=True))
        batch.add_column(sa.Column("priority", sa.String(10), nullable=False, server_default="MEDIUM"))
        batch.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_check_constraint(
            "ck_notifications_priority",
            "priority IN ('LOW','MEDIUM','HIGH','CRITICAL')",
        )
        batch.create_index(
            "ix_notifications_user_type_created",
            ["user_id", "type", "created_at"],
        )
        batch.create_index(
            "ix_notifications_user_priority_created",
            ["user_id", "priority", "created_at"],
        )
        batch.create_index(
            "ix_notifications_reference",
            ["reference_type", "reference_id"],
        )
        batch.create_index(
            "ix_notifications_user_unread_created",
            ["user_id", "is_read", "created_at"],
        )

    op.execute(sa.text(
        "UPDATE notifications SET "
        "reference_type = CASE "
        "WHEN claim_id IS NOT NULL THEN 'claim' "
        "WHEN product_id IS NOT NULL THEN 'product' ELSE NULL END, "
        "reference_id = CASE "
        "WHEN claim_id IS NOT NULL THEN CAST(claim_id AS VARCHAR(80)) "
        "WHEN product_id IS NOT NULL THEN CAST(product_id AS VARCHAR(80)) ELSE NULL END"
    ))

    op.create_table(
        "notification_preferences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("warranty_reminders", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("claim_updates", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("information_requests", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("review_notifications", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", name="uq_notification_preferences_user"),
    )
    op.create_index(
        "ix_notification_preferences_user_id",
        "notification_preferences",
        ["user_id"],
        unique=True,
    )


def downgrade():
    op.drop_index("ix_notification_preferences_user_id", table_name="notification_preferences")
    op.drop_table("notification_preferences")
    with op.batch_alter_table("notifications") as batch:
        batch.drop_index("ix_notifications_user_unread_created")
        batch.drop_index("ix_notifications_reference")
        batch.drop_index("ix_notifications_user_priority_created")
        batch.drop_index("ix_notifications_user_type_created")
        batch.drop_constraint("ck_notifications_priority", type_="check")
        batch.drop_column("deleted_at")
        batch.drop_column("priority")
        batch.drop_column("reference_id")
        batch.drop_column("reference_type")
