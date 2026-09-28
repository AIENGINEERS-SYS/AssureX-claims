"""Centralized database engine and request-scoped session dependency."""
import os
from collections.abc import Iterator
from functools import lru_cache
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if url:
        return url.replace("postgres://", "postgresql+psycopg://", 1) if url.startswith("postgres://") else url
    if any(os.getenv(k) for k in ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")):
        from sqlalchemy.engine import URL
        return URL.create(
            "postgresql+psycopg", username=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD"),
            host=os.getenv("DB_HOST", "localhost"), port=int(os.getenv("DB_PORT", "5432")),
            database=os.getenv("DB_NAME", "assurex"),
        ).render_as_string(hide_password=False)
    return "sqlite:///./assurex_dev.db"


@lru_cache(maxsize=4)
def get_engine(url: str | None = None) -> Engine:
    engine = create_engine(url or database_url(), pool_pre_ping=True)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def enable_foreign_keys(dbapi_connection, connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    return engine


def session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=engine or get_engine(), expire_on_commit=False)


def get_db() -> Iterator[Session]:
    with session_factory()() as session:
        yield session
