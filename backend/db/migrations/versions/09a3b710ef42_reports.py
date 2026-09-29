"""Durable export queue, access manifest and report lookup indexes."""
from alembic import op
import sqlalchemy as sa

revision = "09a3b710ef42"
down_revision = "a24f8e73d51b"
branch_labels = depends_on = None


def upgrade():
    op.create_table("report_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("auth_version", sa.Integer(), nullable=False),
        sa.Column("format", sa.String(10), nullable=False),
        sa.Column("filters", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("processed", sa.Integer(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("error", sa.String(200)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('queued','running','completed','failed','expired')", name="ck_report_status"),
        sa.CheckConstraint("format IN ('csv','excel','pdf')", name="ck_report_format"))
    op.create_index("ix_report_owner_created", "report_jobs", ["user_id", "created_at"])
    op.create_index("ix_report_queue", "report_jobs", ["status", "created_at"])
    op.create_index("ix_report_jobs_expires_at", "report_jobs", ["expires_at"])
    op.create_table("report_items",
        sa.Column("job_id", sa.String(36), sa.ForeignKey("report_jobs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("kind", sa.String(10), primary_key=True),
        sa.Column("resource_id", sa.Integer(), primary_key=True))
    op.create_index("ix_claims_report_employee", "claims", ["assigned_employee_id", "id"])
    op.create_index("ix_claims_report_reviewer", "claims", ["assigned_reviewer_id", "id"])


def downgrade():
    op.drop_index("ix_claims_report_reviewer", table_name="claims")
    op.drop_index("ix_claims_report_employee", table_name="claims")
    op.drop_table("report_items")
    op.drop_table("report_jobs")
