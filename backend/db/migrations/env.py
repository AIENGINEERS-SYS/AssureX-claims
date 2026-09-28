from logging.config import fileConfig
from alembic import context
from flask import current_app, has_app_context
from sqlalchemy import create_engine, pool
from backend.db import Base
from backend.db.session import database_url

config = context.config
url = current_app.config["SQLALCHEMY_DATABASE_URI"] if has_app_context() else database_url()
config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
if config.config_file_name is not None and config.get_section("loggers"):
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(url, poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            sqlite = connection.dialect.name == "sqlite"
            if sqlite:
                # Batch recreation of referenced tables needs FK checks disabled
                # on this migration connection only. Validate before committing.
                connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
                connection.commit()
            with connection.begin():
                if sqlite:
                    connection.exec_driver_sql("BEGIN")
                context.configure(connection=connection, target_metadata=target_metadata,
                                  render_as_batch=sqlite, compare_type=True)
                with context.begin_transaction():
                    context.run_migrations()
                if sqlite and connection.exec_driver_sql("PRAGMA foreign_key_check").fetchone():
                    raise RuntimeError("Migration would leave invalid foreign keys")
            if sqlite:
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                connection.commit()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
