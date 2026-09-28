"""Phase 6 migration preserves evidence and refuses ambiguous duplicate history."""
from datetime import date, datetime, timezone
import pytest
from alembic import command
from sqlalchemy import MetaData, inspect, select
from test_auth_migrations import legacy_database
from test_product_migrations import legacy_products


def phase5_database(tmp_path, monkeypatch):
    cfg, engine, tables = legacy_database(tmp_path, monkeypatch)
    legacy_products(cfg, engine, tables)
    command.upgrade(cfg, "d582ab901c42")
    metadata = MetaData(); metadata.reflect(engine)
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        connection.execute(metadata.tables["claims"].insert(), dict(id=1, claim_id="DRF-legacy", user_id=1,
            product_id=1, warranty_id=1, status="draft", current_step=3, version=1,
            manual_review_required=False, created_at=now, updated_at=now))
    return cfg, engine, metadata, now


def legacy_document(table, now, identifier=1, digest="a" * 64):
    return dict(id=identifier, document_id=f"DOC-{identifier}", claim_id=1, document_type="receipt",
        original_filename=f"receipt-{identifier}.png", stored_filename=f"random-{identifier}.png",
        mime_type="image/png", file_size=100, storage_path=f"claims/1/random-{identifier}.png",
        file_hash=digest, ocr_status="pending", uploaded_by=1, created_at=now, updated_at=now)


def test_document_migration_preserves_existing_rows_and_round_trips(tmp_path, monkeypatch):
    cfg, engine, metadata, now = phase5_database(tmp_path, monkeypatch)
    with engine.begin() as connection:
        connection.execute(metadata.tables["documents"].insert(), legacy_document(metadata.tables["documents"], now))
    command.upgrade(cfg, "head")
    migrated = MetaData(); migrated.reflect(engine)
    with engine.connect() as connection:
        row = connection.execute(select(migrated.tables["documents"])).mappings().one()
        assert row["original_filename"] == "receipt-1.png"
        assert row["upload_status"] == "stored" and row["review_status"] == "not_required"
        assert row["cross_claim_duplicate"] is False
        assert not connection.exec_driver_sql("PRAGMA foreign_key_check").all()
    command.downgrade(cfg, "d582ab901c42")
    columns = {column["name"] for column in inspect(engine).get_columns("documents")}
    assert "review_status" not in columns and "ocr_text" in columns
    command.upgrade(cfg, "head")
    engine.dispose()


def test_document_migration_rejects_same_claim_duplicate_hashes(tmp_path, monkeypatch):
    cfg, engine, metadata, now = phase5_database(tmp_path, monkeypatch)
    with engine.begin() as connection:
        connection.execute(metadata.tables["documents"].insert(), [
            legacy_document(metadata.tables["documents"], now, 1),
            legacy_document(metadata.tables["documents"], now, 2)])
    with pytest.raises(RuntimeError, match="duplicate document content"):
        command.upgrade(cfg, "head")
    assert "upload_status" not in {column["name"] for column in inspect(engine).get_columns("documents")}
    engine.dispose()
