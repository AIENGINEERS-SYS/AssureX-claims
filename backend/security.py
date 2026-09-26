"""JWT middleware and database-backed authorization."""
from datetime import datetime, timezone
from functools import wraps
from flask import jsonify
from flask_jwt_extended import current_user, verify_jwt_in_request
from sqlalchemy import select
from werkzeug.exceptions import Forbidden
from backend.extensions import db, jwt
from backend.db.auth_models import AuthSession, RevokedToken
from backend.db.models import User, utcnow


def role_required(*roles):
    """Authenticate first, authorize using the current DB role; admins inherit access."""
    if not roles or not set(roles) <= {"customer", "employee", "reviewer", "admin"}:
        raise ValueError("role_required needs known roles")

    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            verify_jwt_in_request()
            if current_user.role != "admin" and current_user.role not in roles:
                raise Forbidden("Your role cannot access this resource.")
            return fn(*args, **kwargs)
        return wrapped
    return decorate


def init_jwt_callbacks():
    def failure(message):
        return jsonify(error={"code": "unauthorized", "message": message}), 401

    @jwt.token_in_blocklist_loader
    def blocked(header, payload):
        sid = payload.get("sid")
        if not isinstance(sid, str) or len(sid) != 36:
            return True
        if db.session.get(RevokedToken, payload["jti"]) is not None:
            return True
        session = db.session.scalar(select(AuthSession).where(
            AuthSession.id == sid, AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > utcnow()))
        if session is None or str(session.user_id) != payload["sub"]:
            return True
        return payload["type"] == "refresh" and session.refresh_jti != payload["jti"]

    @jwt.user_lookup_loader
    def lookup(header, payload):
        identity = payload.get("sub", "")
        if not isinstance(identity, str) or not identity.isascii() or not identity.isdigit() or len(identity) > 18:
            return None
        user = db.session.get(User, int(identity))
        if user and user.is_active and payload.get("ver") == user.auth_version:
            return user
        return None

    jwt.unauthorized_loader(lambda reason: failure("Authentication is required."))
    jwt.invalid_token_loader(lambda reason: failure("Invalid token."))
    jwt.expired_token_loader(lambda header, payload: failure("Token has expired."))
    jwt.revoked_token_loader(lambda header, payload: failure("Token has been revoked."))
    jwt.user_lookup_error_loader(lambda header, payload: failure("Account or credentials are no longer valid."))
    jwt.needs_fresh_token_loader(lambda header, payload: failure("Please log in again for this action."))


def token_expiry(payload):
    return datetime.fromtimestamp(payload["exp"], timezone.utc)
