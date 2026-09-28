"""Role dashboards, notifications and duplicate decisions.

Revision ID: f23d7c9a102b
Revises: b761a04c9f2e
"""
from alembic import op
import sqlalchemy as sa

revision = "f23d7c9a102b"
down_revision = "b761a04c9f2e"
branch_labels = depends_on = None


def upgrade():
    with op.batch_alter_table("claims") as batch:
        batch.add_column(sa.Column("assigned_reviewer_id", sa.Integer()))
        batch.create_foreign_key("fk_claims_assigned_reviewer", "users", ["assigned_reviewer_id"], ["id"], ondelete="RESTRICT")
        batch.create_index("ix_claims_assigned_reviewer_id", ["assigned_reviewer_id"])
        batch.create_index("ix_claims_user_updated", ["user_id", "updated_at"])
        batch.create_index("ix_claims_review_queue", ["status", "assigned_reviewer_id", "submitted_at"])
    with op.batch_alter_table("notifications") as batch:
        batch.add_column(sa.Column("product_id", sa.Integer()))
        batch.add_column(sa.Column("dedupe_key", sa.String(160)))
        batch.create_foreign_key("fk_notifications_product", "products", ["product_id"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_notifications_product_id", ["product_id"])
        batch.create_unique_constraint("uq_notifications_dedupe_key", ["dedupe_key"])
        batch.create_index("ix_notifications_user_created", ["user_id", "created_at"])
    with op.batch_alter_table("reviews") as batch:
        batch.create_index("ix_reviews_reviewer_date", ["reviewer_user_id", "reviewed_at"])
    op.create_table("duplicate_investigations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("claim_id", sa.Integer(), sa.ForeignKey("claims.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("matching_claim_id", sa.Integer(), sa.ForeignKey("claims.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("reviewer_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("claim_id < matching_claim_id", name="ck_duplicate_pair_order"),
        sa.CheckConstraint("status IN ('confirmed','false_positive')", name="ck_duplicate_status"),
        sa.UniqueConstraint("claim_id", "matching_claim_id", "file_hash", name="uq_duplicate_pair_hash"))
    op.create_index("ix_duplicate_investigations_claim_id", "duplicate_investigations", ["claim_id"])
    op.create_index("ix_duplicate_investigations_matching_claim_id", "duplicate_investigations", ["matching_claim_id"])


def downgrade():
    op.drop_index("ix_duplicate_investigations_matching_claim_id", table_name="duplicate_investigations")
    op.drop_index("ix_duplicate_investigations_claim_id", table_name="duplicate_investigations")
    op.drop_table("duplicate_investigations")
    with op.batch_alter_table("reviews") as batch:
        batch.drop_index("ix_reviews_reviewer_date")
    with op.batch_alter_table("notifications") as batch:
        batch.drop_index("ix_notifications_user_created")
        batch.drop_constraint("uq_notifications_dedupe_key", type_="unique")
        batch.drop_index("ix_notifications_product_id")
        batch.drop_constraint("fk_notifications_product", type_="foreignkey")
        batch.drop_column("dedupe_key")
        batch.drop_column("product_id")
    with op.batch_alter_table("claims") as batch:
        batch.drop_index("ix_claims_review_queue")
        batch.drop_index("ix_claims_user_updated")
        batch.drop_index("ix_claims_assigned_reviewer_id")
        batch.drop_constraint("fk_claims_assigned_reviewer", type_="foreignkey")
        batch.drop_column("assigned_reviewer_id")
