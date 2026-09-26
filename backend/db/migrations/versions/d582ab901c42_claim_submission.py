"""Resumable claim submission; preserve existing claims and evidence.

Revision ID: d582ab901c42
Revises: c731ef4209ab
"""
from alembic import op
import sqlalchemy as sa
import re

revision = "d582ab901c42"
down_revision = "c731ef4209ab"
branch_labels = depends_on = None


def upgrade():
    with op.batch_alter_table("products") as batch:
        batch.add_column(sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
    with op.batch_alter_table("claims") as batch:
        for name, kind in (("product_id", sa.Integer()), ("warranty_id", sa.Integer()),
                           ("fault_date", sa.Date()), ("fault_type", sa.String(100)),
                           ("fault_description", sa.Text())):
            batch.alter_column(name, existing_type=kind, nullable=True)
        batch.add_column(sa.Column("repair_history", sa.Text()))
        batch.add_column(sa.Column("previous_replacement", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("submitted_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("current_step", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
        batch.create_check_constraint("ck_claims_step", "current_step BETWEEN 1 AND 4")
    op.create_table("claim_sequences", sa.Column("year", sa.Integer(), primary_key=True),
                    sa.Column("value", sa.Integer(), nullable=False))
    # Preserve numbering after a downgrade/re-upgrade or imported historical IDs.
    counters = {}
    for identifier in op.get_bind().execute(sa.text("SELECT claim_id FROM claims")).scalars():
        match = re.fullmatch(r"CLM-(\d{4})-(\d{6,})", identifier)
        if match:
            year, value = map(int, match.groups())
            counters[year] = max(counters.get(year, 0), value)
    for year, value in counters.items():
        op.get_bind().execute(sa.text("INSERT INTO claim_sequences (year, value) VALUES (:year, :value)"),
                              {"year": year, "value": value})


def downgrade():
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT COUNT(*) FROM claims WHERE product_id IS NULL OR warranty_id IS NULL "
                                 "OR fault_date IS NULL OR fault_type IS NULL OR fault_description IS NULL")):
        raise RuntimeError("Complete or export incomplete drafts before downgrading Phase 5")
    op.drop_table("claim_sequences")
    with op.batch_alter_table("claims") as batch:
        batch.drop_constraint("ck_claims_step", type_="check")
        for name in ("repair_history", "previous_replacement", "submitted_at", "current_step", "version"):
            batch.drop_column(name)
        for name, kind in (("product_id", sa.Integer()), ("warranty_id", sa.Integer()),
                           ("fault_date", sa.Date()), ("fault_type", sa.String(100)),
                           ("fault_description", sa.Text())):
            batch.alter_column(name, existing_type=kind, nullable=False)
    with op.batch_alter_table("products") as batch:
        batch.drop_column("is_active")
