"""Optional production trigram indexes; kept out of transactional migrations."""
INDEXES = {
    'products': ('name', 'serial_number', 'brand', 'model_number', 'product_id'),
    'users': ('full_name', 'user_id'),
    'claims': ('claim_id',),
    'warranties': ('provider', 'warranty_id'),
}


def install(engine):
    # All identifiers are constants; no caller or request supplies SQL fragments.
    with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
        connection.exec_driver_sql('CREATE EXTENSION IF NOT EXISTS pg_trgm')
        for table, columns in INDEXES.items():
            for column in columns:
                connection.exec_driver_sql(f'CREATE INDEX CONCURRENTLY IF NOT EXISTS '
                    f'ix_search_{table}_{column}_trgm ON {table} USING gin ({column} gin_trgm_ops)')
