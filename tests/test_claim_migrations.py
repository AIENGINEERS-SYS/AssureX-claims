"""Phase 5 retains existing IDs/evidence and guards lossy downgrades."""
from datetime import date, datetime, timezone
import pytest
from alembic import command
from sqlalchemy import MetaData, inspect, select
from test_auth_migrations import legacy_database
from test_product_migrations import legacy_products


def test_migration_preserves_claims_and_round_trips(tmp_path, monkeypatch):
    cfg, engine, tables = legacy_database(tmp_path, monkeypatch)
    legacy_products(cfg, engine, tables)
    command.upgrade(cfg, "c731ef4209ab")
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        connection.execute(tables.tables["claims"].insert(), dict(id=1, claim_id="CLM-2026-000042", user_id=1,
            product_id=1, warranty_id=1, fault_date=date(2026, 1, 1), fault_type="power",
            fault_description="Old evidence", status="submitted", manual_review_required=False,
            created_at=now, updated_at=now))
    command.upgrade(cfg, "head")
    migrated = MetaData(); migrated.reflect(engine)
    with engine.connect() as connection:
        claim = connection.execute(select(migrated.tables["claims"])).mappings().one()
        assert claim["claim_id"] == "CLM-2026-000042" and claim["fault_description"] == "Old evidence"
        sequence = connection.execute(select(migrated.tables["claim_sequences"])).mappings().one()
        assert sequence["year"] == 2026 and sequence["value"] == 42
        assert claim["version"] == 1 and claim["previous_replacement"] is False
        assert not connection.exec_driver_sql("PRAGMA foreign_key_check").all()
    command.downgrade(cfg, "c731ef4209ab")
    assert "version" not in {column["name"] for column in inspect(engine).get_columns("claims")}
    command.upgrade(cfg, "head")
    engine.dispose()


def test_downgrade_rejects_incomplete_drafts_before_changes(tmp_path, monkeypatch):
    cfg, engine, tables = legacy_database(tmp_path, monkeypatch)
    legacy_products(cfg, engine, tables)
    command.upgrade(cfg, "head")
    migrated = MetaData(); migrated.reflect(engine)
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        connection.execute(migrated.tables["claims"].insert(), dict(claim_id="DRF-1", user_id=1,
            status="draft", manual_review_required=False, created_at=now, updated_at=now))
    with pytest.raises(RuntimeError, match="incomplete drafts"):
        command.downgrade(cfg, "c731ef4209ab")
    assert "claim_sequences" in inspect(engine).get_table_names()
    engine.dispose()
