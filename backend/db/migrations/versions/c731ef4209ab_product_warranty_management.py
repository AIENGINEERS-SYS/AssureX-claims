"""Product serial identity and explicit warranty duration units.

Revision ID: c731ef4209ab
Revises: 8b42d17c9a03
"""
import hashlib
import json
import unicodedata
from alembic import op
import sqlalchemy as sa

revision = "c731ef4209ab"
down_revision = "8b42d17c9a03"
branch_labels = depends_on = None


def upgrade():
    connection = op.get_bind()
    # Keep normalization local to the migration so future code changes cannot alter history.
    identities, seen = [], set()
    for row in connection.execute(sa.text("SELECT id, brand, model_number, serial_number FROM products")):
        values = [unicodedata.normalize("NFKC", value).strip().casefold() for value in row[1:]]
        key = hashlib.sha256(json.dumps(values, ensure_ascii=True).encode("utf-8")).hexdigest()
        if key in seen:
            raise RuntimeError("Resolve duplicate brand/model/serial combinations before upgrading Phase 4")
        seen.add(key)
        identities.append({"id": row.id, "serial_key": key})
    with op.batch_alter_table("products") as batch:
        batch.add_column(sa.Column("serial_key", sa.String(64), nullable=True))
    if identities:
        connection.execute(sa.text("UPDATE products SET serial_key=:serial_key WHERE id=:id"), identities)
    with op.batch_alter_table("products") as batch:
        batch.alter_column("serial_key", existing_type=sa.String(64), nullable=False)
        batch.create_unique_constraint("uq_products_serial_key", ["serial_key"])
    with op.batch_alter_table("warranties") as batch:
        batch.add_column(sa.Column("duration_unit", sa.String(10), server_default="months", nullable=False))
        batch.create_check_constraint("ck_warranties_duration_unit", "duration_unit IN ('months','years')")
    with op.batch_alter_table("warranties") as batch:
        batch.alter_column("duration_unit", existing_type=sa.String(10), server_default=None)


def downgrade():
    with op.batch_alter_table("warranties") as batch:
        batch.drop_constraint("ck_warranties_duration_unit", type_="check")
        batch.drop_column("duration_unit")
    with op.batch_alter_table("products") as batch:
        batch.drop_constraint("uq_products_serial_key", type_="unique")
        batch.drop_column("serial_key")
