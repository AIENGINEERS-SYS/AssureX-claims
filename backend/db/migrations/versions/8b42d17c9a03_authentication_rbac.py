"""Authentication sessions, normalized roles and employee assignments.

Revision ID: 8b42d17c9a03
Revises: 53a58d50e1e4
"""
from alembic import op
import sqlalchemy as sa

revision = "8b42d17c9a03"
down_revision = "53a58d50e1e4"
branch_labels = depends_on = None


def upgrade():
    bind = op.get_bind()
    duplicates = bind.execute(sa.text(
        "SELECT lower(trim(email)) FROM users GROUP BY lower(trim(email)) HAVING count(*) > 1"
    )).first()
    if duplicates:
        raise RuntimeError("Resolve case-insensitive duplicate emails before upgrading")
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("ck_users_role", type_="check")
        batch.add_column(sa.Column("full_name", sa.String(201), nullable=True))
        batch.add_column(sa.Column("auth_version", sa.Integer(), nullable=False, server_default="0"))
    bind.execute(sa.text("UPDATE users SET full_name = trim(first_name || ' ' || last_name), email = lower(trim(email)), "
                         "role = CASE role WHEN 'administrator' THEN 'admin' WHEN 'service_center_employee' THEN 'employee' ELSE role END"))
    with op.batch_alter_table("users") as batch:
        batch.alter_column("full_name", existing_type=sa.String(201), nullable=False)
        batch.alter_column("auth_version", existing_type=sa.Integer(), server_default=None)
        batch.create_check_constraint("ck_users_role", "role IN ('customer','employee','reviewer','admin')")
    with op.batch_alter_table("claims") as batch:
        batch.add_column(sa.Column("assigned_employee_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_claims_assigned_employee", "users", ["assigned_employee_id"], ["id"], ondelete="RESTRICT")
        batch.create_index("ix_claims_assigned_employee_id", ["assigned_employee_id"])
    op.create_table("auth_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("refresh_jti", sa.String(36), unique=True, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])
    op.create_table("revoked_tokens",
        sa.Column("jti", sa.String(36), primary_key=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_revoked_tokens_expires_at", "revoked_tokens", ["expires_at"])


def downgrade():
    op.drop_table("revoked_tokens")
    op.drop_table("auth_sessions")
    with op.batch_alter_table("claims") as batch:
        batch.drop_index("ix_claims_assigned_employee_id")
        batch.drop_constraint("fk_claims_assigned_employee", type_="foreignkey")
        batch.drop_column("assigned_employee_id")
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("ck_users_role", type_="check")
        batch.drop_column("auth_version")
        batch.drop_column("full_name")
    op.execute("UPDATE users SET role = CASE role WHEN 'admin' THEN 'administrator' WHEN 'employee' THEN 'service_center_employee' ELSE role END")
    with op.batch_alter_table("users") as batch:
        batch.create_check_constraint("ck_users_role", "role IN ('customer','service_center_employee','reviewer','administrator')")
