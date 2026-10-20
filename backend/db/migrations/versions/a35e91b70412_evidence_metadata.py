"""Preserve evidence analysis and confirmed purchase provenance.

Revision ID: a35e91b70412
Revises: 2cb08e57a419
"""
from alembic import op
import sqlalchemy as sa

revision = 'a35e91b70412'
down_revision = '2cb08e57a419'
branch_labels = depends_on = None


def upgrade():
    op.add_column('products', sa.Column('purchase_metadata', sa.JSON(), nullable=True))
    op.add_column('documents', sa.Column('processing_metadata', sa.JSON(), nullable=True))
    op.execute(sa.text("UPDATE documents SET product_id = (SELECT product_id FROM claims "
        "WHERE claims.id = documents.claim_id) WHERE product_id IS NULL AND claim_id IS NOT NULL"))
    op.execute(sa.text("UPDATE documents SET warranty_id = (SELECT warranty_id FROM claims "
        "WHERE claims.id = documents.claim_id) WHERE warranty_id IS NULL AND claim_id IS NOT NULL"))


def downgrade():
    op.drop_column('documents', 'processing_metadata')
    op.drop_column('products', 'purchase_metadata')
