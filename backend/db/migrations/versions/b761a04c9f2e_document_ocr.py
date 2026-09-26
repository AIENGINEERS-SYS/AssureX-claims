"""Secure document OCR, duplicate detection and review audit fields.

Revision ID: b761a04c9f2e
Revises: d582ab901c42
"""
from alembic import op
import sqlalchemy as sa

revision = "b761a04c9f2e"
down_revision = "d582ab901c42"
branch_labels = depends_on = None


def upgrade():
    connection = op.get_bind()
    duplicate = connection.execute(sa.text(
        "SELECT claim_id, file_hash FROM documents WHERE claim_id IS NOT NULL "
        "GROUP BY claim_id, file_hash HAVING COUNT(*) > 1 LIMIT 1"
    )).first()
    if duplicate:
        raise RuntimeError("Resolve duplicate document content within claims before applying Phase 6")
    with op.batch_alter_table("documents") as batch:
        batch.add_column(sa.Column("upload_status", sa.String(30), nullable=False, server_default="stored"))
        batch.add_column(sa.Column("ocr_error", sa.Text()))
        batch.add_column(sa.Column("ocr_confidence", sa.Numeric(5, 4)))
        batch.add_column(sa.Column("ocr_provider", sa.String(80)))
        batch.add_column(sa.Column("ocr_provider_version", sa.String(80)))
        batch.add_column(sa.Column("ocr_processed_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("processing_duration_ms", sa.Integer()))
        batch.add_column(sa.Column("review_status", sa.String(30), nullable=False, server_default="not_required"))
        batch.add_column(sa.Column("reviewed_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("reviewed_by", sa.Integer()))
        batch.add_column(sa.Column("cross_claim_duplicate", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.create_foreign_key("fk_documents_reviewed_by_users", "users", ["reviewed_by"], ["id"], ondelete="RESTRICT")
        batch.create_index("ix_documents_reviewed_by", ["reviewed_by"])
        batch.create_unique_constraint("uq_documents_claim_hash", ["claim_id", "file_hash"])
        batch.create_check_constraint("ck_documents_upload_status", "upload_status IN ('stored')")
        batch.create_check_constraint("ck_documents_ocr_status", "ocr_status IN ('pending','processing','completed','completed_with_warnings','failed','review_required')")
        batch.create_check_constraint("ck_documents_review_status", "review_status IN ('pending','confirmed','not_required')")
        batch.create_check_constraint("ck_documents_processing_duration", "processing_duration_ms IS NULL OR processing_duration_ms >= 0")
        batch.create_check_constraint("ck_documents_ocr_confidence", "ocr_confidence IS NULL OR (ocr_confidence >= 0 AND ocr_confidence <= 1)")


def downgrade():
    with op.batch_alter_table("documents") as batch:
        for name in ("ck_documents_ocr_confidence", "ck_documents_processing_duration",
                     "ck_documents_review_status", "ck_documents_ocr_status", "ck_documents_upload_status"):
            batch.drop_constraint(name, type_="check")
        batch.drop_constraint("uq_documents_claim_hash", type_="unique")
        batch.drop_index("ix_documents_reviewed_by")
        batch.drop_constraint("fk_documents_reviewed_by_users", type_="foreignkey")
        for name in ("cross_claim_duplicate", "reviewed_by", "reviewed_at", "review_status",
                     "processing_duration_ms", "ocr_processed_at", "ocr_provider_version",
                     "ocr_provider", "ocr_confidence", "ocr_error", "upload_status"):
            batch.drop_column(name)
