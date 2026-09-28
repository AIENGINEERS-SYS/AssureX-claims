"""Phase 23 migration preserves claims and notifications through upgrade and downgrade."""
from alembic import command
from sqlalchemy import MetaData, inspect, select
from test_document_migrations import phase5_database, legacy_document


def test_dashboard_migration_round_trip(tmp_path, monkeypatch):
    cfg, engine, metadata, now = phase5_database(tmp_path, monkeypatch)
    with engine.begin() as connection:
        connection.execute(metadata.tables["documents"].insert(), legacy_document(metadata.tables["documents"], now))
    command.upgrade(cfg, "b761a04c9f2e")
    before = MetaData();before.reflect(engine)
    with engine.begin() as connection:
        connection.execute(before.tables["notifications"].insert(), dict(id=1, notification_id="NTF-legacy",
            user_id=1, claim_id=1, type="claim_update", title="Existing", message="Still visible",
            is_read=False, created_at=now))
    command.upgrade(cfg, "head")
    after = MetaData();after.reflect(engine)
    with engine.connect() as connection:
        assert connection.execute(select(after.tables["claims"])).mappings().one()["assigned_reviewer_id"] is None
        assert connection.execute(select(after.tables["notifications"])).mappings().one()["message"] == "Still visible"
        assert not connection.exec_driver_sql("PRAGMA foreign_key_check").all()
    command.downgrade(cfg, "b761a04c9f2e")
    assert "assigned_reviewer_id" not in {c["name"] for c in inspect(engine).get_columns("claims")}
    command.upgrade(cfg, "head")
    engine.dispose()
