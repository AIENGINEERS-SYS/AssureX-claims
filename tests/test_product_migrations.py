from datetime import date, datetime, timezone
import pytest
from alembic import command
from sqlalchemy import MetaData, inspect, select
from test_auth_migrations import legacy_database, old_user


def legacy_products(cfg, engine, tables, *, duplicate=False):
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        connection.execute(tables.tables["users"].insert(), old_user(1,"owner@example.com","customer"))
        base = dict(user_id=1,name="Television",category="Electronics",brand="Example",model_number="TV-42",
            serial_number="SERIAL-1",purchase_date=date(2025,1,1),purchase_price=100,retailer="Shop",created_at=now,updated_at=now)
        connection.execute(tables.tables["products"].insert(),base | {"id":1,"product_id":"PRD-1"})
        if duplicate:
            connection.execute(tables.tables["products"].insert(),base | {"id":2,"product_id":"PRD-2","brand":"example ","serial_number":"serial-1"})
        connection.execute(tables.tables["warranties"].insert(),dict(id=1,warranty_id="WAR-1",product_id=1,
            provider="Legacy",warranty_type="standard",start_date=date(2025,1,1),expiry_date=date(2027,1,1),
            coverage_duration_months=24,coverage_conditions={"parts":True},exclusions=[],extended_warranty=False,
            created_at=now,updated_at=now))
    command.upgrade(cfg,"8b42d17c9a03")


def test_product_migration_preserves_existing_warranty_dates(tmp_path,monkeypatch):
    cfg,engine,tables = legacy_database(tmp_path,monkeypatch)
    legacy_products(cfg,engine,tables)
    command.upgrade(cfg,"head")
    migrated = MetaData(); migrated.reflect(engine)
    with engine.connect() as connection:
        product = connection.execute(select(migrated.tables["products"])).mappings().one()
        warranty = connection.execute(select(migrated.tables["warranties"])).mappings().one()
        assert len(product["serial_key"]) == 64
        assert warranty["expiry_date"] == date(2027,1,1) and warranty["duration_unit"] == "months"
        assert warranty["coverage_conditions"] == {"parts":True}
        assert not connection.exec_driver_sql("PRAGMA foreign_key_check").all()
    command.downgrade(cfg,"8b42d17c9a03")
    assert "serial_key" not in {column["name"] for column in inspect(engine).get_columns("products")}
    command.upgrade(cfg,"head")
    engine.dispose()


def test_duplicate_legacy_serials_abort_before_schema_changes(tmp_path,monkeypatch):
    cfg,engine,tables = legacy_database(tmp_path,monkeypatch)
    legacy_products(cfg,engine,tables,duplicate=True)
    with pytest.raises(RuntimeError,match="duplicate brand/model/serial"):
        command.upgrade(cfg,"head")
    assert "serial_key" not in {column["name"] for column in inspect(engine).get_columns("products")}
    engine.dispose()
