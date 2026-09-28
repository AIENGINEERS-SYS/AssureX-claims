"""Migration checks protect existing Phase 2 data and both migration entry points."""
from datetime import date, datetime, timezone
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, create_engine, inspect, select
from backend import create_app
from backend.db.models import User
from backend.extensions import db


def legacy_database(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'legacy.sqlite3'}"
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config("backend/db/alembic.ini")
    command.upgrade(cfg, "53a58d50e1e4")
    engine = create_engine(url)
    metadata = MetaData()
    metadata.reflect(engine)
    return cfg, engine, metadata


def old_user(id, email, role):
    now = datetime.now(timezone.utc)
    return dict(id=id, user_id=f"USR-{id}", email=email, role=role,
        first_name="Legacy", last_name="User", password_hash="legacy-hash-needs-reset",
        is_active=True, created_at=now, updated_at=now)


def test_upgrade_populated_database_and_downgrade(tmp_path, monkeypatch):
    cfg, engine, tables = legacy_database(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        connection.execute(tables.tables["users"].insert(), [
            old_user(1, "ADMIN@Example.com", "administrator"),
            old_user(2, "Employee@example.com", "service_center_employee"),
            old_user(3, "Customer@example.com", "customer")])
        connection.execute(tables.tables["products"].insert(), dict(
            id=1, product_id="PRD-legacy", user_id=3, name="Laptop", category="electronics", brand="Example",
            model_number="M1", serial_number="S1", purchase_date=date(2026, 1, 1), purchase_price=100,
            retailer="Shop", created_at=now, updated_at=now))
        connection.execute(tables.tables["warranties"].insert(), dict(
            id=1, warranty_id="WAR-legacy", product_id=1, provider="Example", warranty_type="standard",
            start_date=date(2026, 1, 1), expiry_date=date(2027, 1, 1), coverage_duration_months=12,
            coverage_conditions={}, exclusions=[], extended_warranty=False, created_at=now, updated_at=now))
        connection.execute(tables.tables["claims"].insert(), dict(
            id=1, claim_id="CLM-legacy", user_id=3, product_id=1, warranty_id=1, fault_date=date(2026, 5, 1),
            fault_type="power", fault_description="No power", status="submitted", manual_review_required=False,
            created_at=now, updated_at=now))
    command.upgrade(cfg, "head")
    with engine.connect() as connection:
        rows = connection.execute(select(User.__table__).order_by(User.id)).mappings().all()
        assert [row["role"] for row in rows] == ["admin", "employee", "customer"]
        assert rows[0]["full_name"] == "Legacy User"
        assert rows[0]["email"] == "admin@example.com" and rows[0]["auth_version"] == 0
        assert connection.execute(select(tables.tables["claims"].c.claim_id)).scalar_one() == "CLM-legacy"
        assert not connection.exec_driver_sql("PRAGMA foreign_key_check").all()
    command.downgrade(cfg, "53a58d50e1e4")
    with engine.connect() as connection:
        assert connection.execute(select(tables.tables["users"].c.role).where(tables.tables["users"].c.id == 1)).scalar_one() == "administrator"
    command.upgrade(cfg, "head")
    engine.dispose()


def test_case_collision_aborts_without_partial_changes(tmp_path, monkeypatch):
    cfg, engine, tables = legacy_database(tmp_path, monkeypatch)
    with engine.begin() as connection:
        connection.execute(tables.tables["users"].insert(), [
            old_user(1, "same@example.com", "customer"), old_user(2, "SAME@example.com", "customer")])
    with pytest.raises(RuntimeError, match="duplicate emails"):
        command.upgrade(cfg, "head")
    assert "full_name" not in {column["name"] for column in inspect(engine).get_columns("users")}
    engine.dispose()


def test_flask_migrate_upgrade_and_schema_match(tmp_path, monkeypatch):
    # Flask configuration must win over a conflicting standalone DATABASE_URL.
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'unused.sqlite3'}")
    app = create_app({"TESTING": True, "ASSUREX_ENV": "development", "JWT_SECRET_KEY": "migration-key-" * 5,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'flask.sqlite3'}",
        "BCRYPT_LOG_ROUNDS": 4, "RATELIMIT_ENABLED": False})
    runner = app.test_cli_runner()
    upgrade = runner.invoke(args=["db", "upgrade"])
    assert upgrade.exit_code == 0, upgrade.output
    check = runner.invoke(args=["db", "check"])
    assert check.exit_code == 0, check.output
    assert not (tmp_path / "unused.sqlite3").exists()
    with app.app_context():
        assert "auth_sessions" in inspect(db.engine).get_table_names()
        db.session.remove()
        db.engine.dispose()
