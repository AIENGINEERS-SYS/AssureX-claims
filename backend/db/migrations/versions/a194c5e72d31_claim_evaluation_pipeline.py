"""Persist automated evaluations and reviewer override reasons.

Revision ID: a194c5e72d31
Revises: f23d7c9a102b
"""
from alembic import op
import sqlalchemy as sa

revision = "a194c5e72d31"
down_revision = "f23d7c9a102b"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "evaluation_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("evaluation_id", sa.String(32), nullable=False, unique=True),
        sa.Column("claim_id", sa.Integer(), nullable=False),
        sa.Column("python_prediction_id", sa.Integer()),
        sa.Column("gtm_prediction_id", sa.Integer()),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("recommendation", sa.String(32), nullable=False),
        sa.Column("comparison", sa.JSON(), nullable=False),
        sa.Column("duplicate_finding", sa.JSON(), nullable=False),
        sa.Column("contradictions", sa.JSON(), nullable=False),
        sa.Column("explanation", sa.JSON(), nullable=False),
        sa.Column("model_errors", sa.JSON(), nullable=False),
        sa.Column("policy_code", sa.String(100)),
        sa.Column("policy_version", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "recommendation IN ('likely_valid','likely_invalid','manual_review_required')",
            name="ck_evaluations_recommendation",
        ),
        sa.CheckConstraint("status IN ('complete','partial','failed')", name="ck_evaluations_status"),
        sa.ForeignKeyConstraint(["claim_id"], ["claims.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["python_prediction_id"], ["python_predictions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["gtm_prediction_id"], ["gtm_predictions.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_evaluation_results_claim_id", "evaluation_results", ["claim_id"])
    op.create_index("ix_evaluation_results_python_prediction_id", "evaluation_results", ["python_prediction_id"])
    op.create_index("ix_evaluation_results_gtm_prediction_id", "evaluation_results", ["gtm_prediction_id"])
    op.create_index("ix_evaluation_claim_created", "evaluation_results", ["claim_id", "created_at"])
    with op.batch_alter_table("reviews") as batch:
        batch.add_column(sa.Column("override_reason", sa.Text()))


def downgrade():
    with op.batch_alter_table("reviews") as batch:
        batch.drop_column("override_reason")
    op.drop_index("ix_evaluation_claim_created", table_name="evaluation_results")
    op.drop_index("ix_evaluation_results_gtm_prediction_id", table_name="evaluation_results")
    op.drop_index("ix_evaluation_results_python_prediction_id", table_name="evaluation_results")
    op.drop_index("ix_evaluation_results_claim_id", table_name="evaluation_results")
    op.drop_table("evaluation_results")
