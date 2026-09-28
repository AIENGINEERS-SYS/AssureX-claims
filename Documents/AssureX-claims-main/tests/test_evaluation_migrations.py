"""Non-production upgrade/downgrade and preservation of existing machine/review data."""
from alembic import command
from sqlalchemy import MetaData, inspect, select
from test_document_migrations import phase5_database


def test_evaluation_migration_round_trip_preserves_legacy(tmp_path, monkeypatch):
    cfg, engine, metadata, now = phase5_database(tmp_path, monkeypatch)
    command.upgrade(cfg, "f23d7c9a102b")
    before = MetaData()
    before.reflect(engine)
    with engine.begin() as connection:
        connection.execute(before.tables["claims"].update().values(final_decision="likely_invalid"))
        connection.execute(before.tables["reviews"].insert(), dict(id=1, review_id="REV-legacy",
            claim_id=1, reviewer_user_id=1, decision="approve", comments="Legacy review",
            override_applied=True, previous_decision="likely_invalid", reviewed_at=now, created_at=now))
    command.upgrade(cfg, "head")
    after = MetaData()
    after.reflect(engine)
    with engine.connect() as connection:
        review = connection.execute(select(after.tables["reviews"])).mappings().one()
        assert review["previous_decision"] == "likely_invalid" and review["evaluation_id"] is None
        assert connection.execute(select(after.tables["claims"].c.final_decision)).scalar() == "likely_invalid"
        assert not connection.exec_driver_sql("PRAGMA foreign_key_check").all()
    command.downgrade(cfg, "f23d7c9a102b")
    assert "claim_evaluations" not in inspect(engine).get_table_names()
    command.upgrade(cfg, "head")
    engine.dispose()
