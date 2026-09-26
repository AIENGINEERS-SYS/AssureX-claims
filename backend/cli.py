"""Operator commands never embed credentials in source or shell arguments."""
import click
from sqlalchemy import delete, select
from backend.db.auth_models import AuthSession, RevokedToken
from backend.db.models import AuditLog, User, utcnow
from backend.extensions import db
from .api.common import new_user
from .api.schemas import RegisterSchema
from marshmallow import ValidationError


def init_cli(app):
    @app.cli.command("create-admin")
    @click.option("--email", prompt=True)
    @click.option("--full-name", prompt=True)
    @click.password_option()
    def create_admin(email, full_name, password):
        """Bootstrap an administrator via trusted operator access."""
        try:
            data = RegisterSchema().load(dict(email=email, full_name=full_name, password=password))
        except ValidationError as exc:
            raise click.ClickException(str(exc.messages)) from None
        if db.session.scalar(select(User.id).where(User.email == data["email"])):
            raise click.ClickException("Email already exists.")
        user = new_user(data, "admin")
        db.session.add(AuditLog(user_id=user.id, action="operator.admin.create", entity_type="users", entity_id=str(user.id)))
        db.session.commit()
        click.echo("Administrator created.")

    @app.cli.command("reset-password")
    @click.option("--email", prompt=True)
    @click.password_option()
    def reset_password(email, password):
        """Reset a legacy or lost password and invalidate all user tokens."""
        user = db.session.scalar(select(User).where(User.email == email.strip().lower()))
        if user is None:
            raise click.ClickException("User not found.")
        try:
            user.set_password(password)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from None
        db.session.add(AuditLog(user_id=user.id, action="operator.password.reset", entity_type="users", entity_id=str(user.id)))
        db.session.commit()
        click.echo("Password reset; previous tokens are invalid.")

    @app.cli.command("prune-auth")
    def prune_auth():
        """Remove expired tokens/sessions only; schedule daily."""
        db.session.execute(delete(RevokedToken).where(RevokedToken.expires_at < utcnow()))
        db.session.execute(delete(AuthSession).where(AuthSession.expires_at < utcnow()))
        db.session.commit()
        click.echo("Expired authentication records removed.")
