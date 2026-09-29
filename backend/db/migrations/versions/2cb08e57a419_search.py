"""Private saved searches, search telemetry and bounded lookup indexes."""
from alembic import op
import sqlalchemy as sa

revision = '2cb08e57a419'
down_revision = '09a3b710ef42'
branch_labels = depends_on = None

INDEXES = [
    ('ix_claims_search_owner_created', 'claims', ['user_id', 'created_at', 'id']),
    ('ix_claims_search_created', 'claims', ['created_at', 'id']),
    ('ix_claims_search_updated', 'claims', ['updated_at', 'id']),
    ('ix_products_search_owner_created', 'products', ['user_id', 'created_at', 'id']),
    ('ix_products_search_created', 'products', ['created_at', 'id']),
    ('ix_warranties_search_current', 'warranties', ['product_id', 'start_date', 'expiry_date']),
    ('ix_python_search_latest', 'python_predictions', ['claim_id', 'created_at', 'id']),
    ('ix_python_search_confidence', 'python_predictions', ['top_confidence', 'claim_id']),
    ('ix_reviews_search_date', 'reviews', ['claim_id', 'reviewed_at']),
]


def upgrade():
    op.create_table('saved_searches',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(80), nullable=False), sa.Column('scope', sa.String(20), nullable=False),
        sa.Column('filters', sa.JSON(), nullable=False), sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('user_id', 'name', name='uq_saved_search_name'))
    op.create_index('ix_saved_searches_user_id', 'saved_searches', ['user_id'])
    op.create_table('search_events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('scope', sa.String(20), nullable=False), sa.Column('query', sa.String(120), nullable=False),
        sa.Column('filter_names', sa.JSON(), nullable=False), sa.Column('filters', sa.JSON(), nullable=False),
        sa.Column('elapsed_ms', sa.Integer(), nullable=False), sa.Column('result_count', sa.Integer(), nullable=False),
        sa.Column('outcome', sa.String(10), nullable=False), sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("outcome IN ('success','empty','invalid','error')", name='ck_search_event_outcome'))
    op.create_index('ix_search_events_user_created', 'search_events', ['user_id', 'created_at'])
    op.create_index('ix_search_events_created', 'search_events', ['created_at'])
    op.create_table('search_filter_uses',
        sa.Column('event_id', sa.Integer(), sa.ForeignKey('search_events.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('name', sa.String(40), primary_key=True))
    for name, table, columns in INDEXES:
        op.create_index(name, table, columns)
    op.create_index('ix_products_serial_lower', 'products', [sa.text('lower(serial_number)')])
    op.create_index('ix_products_category_lower', 'products', [sa.text('lower(category)')])


def downgrade():
    op.drop_index('ix_products_category_lower', table_name='products')
    op.drop_index('ix_products_serial_lower', table_name='products')
    for name, table, _ in reversed(INDEXES):
        op.drop_index(name, table_name=table)
    op.drop_table('search_filter_uses')
    op.drop_table('search_events')
    op.drop_table('saved_searches')
