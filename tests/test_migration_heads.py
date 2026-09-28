"""Migration graph invariants: deployment must always have exactly one Alembic head."""
from alembic.config import Config
from alembic.script import ScriptDirectory


def test_alembic_has_single_head():
    script = ScriptDirectory.from_config(Config("backend/db/alembic.ini"))
    heads = script.get_heads()
    assert len(heads) == 1, f"Expected one Alembic head, found: {heads}"
