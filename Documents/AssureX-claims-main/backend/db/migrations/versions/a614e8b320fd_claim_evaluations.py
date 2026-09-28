"""Preserve automated evidence independently of reviewer adjudication.

Revision ID: a614e8b320fd
Revises: f23d7c9a102b
"""
from alembic import op
import sqlalchemy as sa

revision = "a614e8b320fd"
down_revision = "f23d7c9a102b"
branch_labels = depends_on = None


def upgrade():
    op.create_table("claim_evaluations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("evaluation_id", sa.String(32), nullable=False, unique=True),
        sa.Column("claim_id", sa.Integer(), sa.ForeignKey("claims.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("input_version", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(80), nullable=False),
        sa.Column("python_prediction_id", sa.Integer(), sa.ForeignKey("python_predictions.id", ondelete="RESTRICT")),
        sa.Column("gtm_prediction_id", sa.Integer(), sa.ForeignKey("gtm_predictions.id", ondelete="RESTRICT")),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("comparison", sa.JSON(), nullable=False),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("explanation", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("claim_id", "idempotency_key", name="uq_evaluation_retry"),
        sa.UniqueConstraint("claim_id", "input_version", name="uq_evaluation_version"),
        sa.CheckConstraint("decision IN ('likely_valid','likely_invalid','manual_review_required')", name="ck_evaluation_decision"))
    op.create_index("ix_claim_evaluations_claim_id", "claim_evaluations", ["claim_id"])
    with op.batch_alter_table("reviews") as batch:
        batch.add_column(sa.Column("evaluation_id", sa.Integer()))
        batch.create_foreign_key("fk_reviews_evaluation", "claim_evaluations", ["evaluation_id"], ["id"], ondelete="RESTRICT")
        batch.create_index("ix_reviews_evaluation_id", ["evaluation_id"])
    # Do not reinterpret legacy final_decision values: prior reviewer writes may have
    # destroyed the original recommendation. New snapshots establish provenance.


def downgrade():
    # An empty test/dev database can roll back. Never drop historical decisions.
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM claim_evaluations")).scalar():
        raise RuntimeError("Export and retain evaluation history before downgrading this revision")
    with op.batch_alter_table("reviews") as batch:
        batch.drop_index("ix_reviews_evaluation_id")
        batch.drop_constraint("fk_reviews_evaluation", type_="foreignkey")
        batch.drop_column("evaluation_id")
    op.drop_index("ix_claim_evaluations_claim_id", table_name="claim_evaluations")
    op.drop_table("claim_evaluations")
