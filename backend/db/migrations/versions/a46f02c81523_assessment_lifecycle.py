"""Durable assessments, frozen attempts and claim-specific report versions."""
from datetime import datetime, timezone
from uuid import uuid4
from alembic import op
import sqlalchemy as sa

revision = "a46f02c81523"
down_revision = "a35e91b70412"
branch_labels = depends_on = None


def upgrade():
    op.add_column("claims", sa.Column("information_request", sa.JSON(), nullable=True))
    op.create_table("assessment_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("claim_id", sa.Integer(), sa.ForeignKey("claims.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("requested_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT")),
        sa.Column("evidence_digest", sa.String(64), nullable=False),
        sa.Column("evidence_snapshot", sa.JSON(), nullable=False),
        sa.Column("evaluation_id", sa.Integer(), sa.ForeignKey("evaluation_results.id", ondelete="RESTRICT")),
        sa.Column("evaluation_snapshot", sa.JSON()),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("report_status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("report_attempts", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(80)),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("claim_id", "version", name="uq_assessment_version"),
        sa.CheckConstraint("status IN ('queued','running','retry_wait','completed','failed','superseded')", name="ck_assessment_status"),
        sa.CheckConstraint("report_status IN ('pending','running','available','failed')", name="ck_assessment_report_status"),
        sa.CheckConstraint("attempts >= 0 AND report_attempts >= 0", name="ck_assessment_attempts"))
    op.create_index("ix_assessment_jobs_claim_id", "assessment_jobs", ["claim_id"])
    op.create_index("ix_assessment_ready", "assessment_jobs", ["status", "available_at"])
    active = sa.text("status IN ('queued','running','retry_wait')")
    op.create_index("uq_assessment_active_claim", "assessment_jobs", ["claim_id"], unique=True,
        sqlite_where=active, postgresql_where=active)
    op.create_table("assessment_reports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("assessment_id", sa.String(36), sa.ForeignKey("assessment_jobs.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("claim_id", sa.Integer(), sa.ForeignKey("claims.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("evaluation_id", sa.Integer(), sa.ForeignKey("evaluation_results.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("evidence_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_assessment_reports_claim_id", "assessment_reports", ["claim_id"])
    op.create_table("assessment_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("assessment_id", sa.String(36), sa.ForeignKey("assessment_jobs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("evaluation_id", sa.Integer(), sa.ForeignKey("evaluation_results.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_assessment_attempts_assessment_id", "assessment_attempts", ["assessment_id"])
    # Preserve the former value in immutable audit history before correcting
    # undecided claims populated by the old automated evaluator.
    connection = op.get_bind()
    claims = connection.execute(sa.text("SELECT id, final_decision FROM claims WHERE final_decision IS NOT NULL "
        "AND status NOT IN ('approved','rejected','closed') AND NOT EXISTS "
        "(SELECT 1 FROM reviews WHERE reviews.claim_id = claims.id AND decision IN ('approve','reject'))")).all()
    audit = sa.table("audit_logs", sa.column("audit_id"), sa.column("claim_id"), sa.column("action"),
        sa.column("entity_type"), sa.column("entity_id"), sa.column("old_values", sa.JSON()),
        sa.column("new_values", sa.JSON()), sa.column("created_at", sa.DateTime(timezone=True)))
    for claim_id, previous in claims:
        connection.execute(audit.insert().values(audit_id="AUD-" + uuid4().hex[:24], claim_id=claim_id,
            action="claim.automated_decision_reclassified", entity_type="claims", entity_id=str(claim_id),
            old_values={"final_decision": previous}, new_values={"final_decision": None,
                "reason": "Automated recommendation is not an authorized human decision."},
            created_at=datetime.now(timezone.utc)))
        connection.execute(sa.text("UPDATE claims SET final_decision = NULL WHERE id = :id"), {"id": claim_id})


def downgrade():
    op.drop_table("assessment_attempts")
    op.drop_table("assessment_reports")
    op.drop_table("assessment_jobs")
    op.drop_column("claims", "information_request")
    # Do not restore misleading decisions or remove their audit records.
