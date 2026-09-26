"""Registration, credentials and rotating refresh tokens."""
from uuid import uuid4
from flask import Blueprint, current_app
from flask_jwt_extended import (create_access_token, create_refresh_token, current_user,
                                decode_token, get_jwt, jwt_required)
from sqlalchemy import select, update
from werkzeug.exceptions import Unauthorized
from backend.db.auth_models import AuthSession, RevokedToken
from backend.db.models import User, utcnow
from backend.extensions import bcrypt, db, limiter
from backend.security import token_expiry
from .common import audit, new_user, set_name
from .schemas import (LoginSchema, PasswordSchema, ProfileSchema, RegisterSchema,
                      body, user_json)

bp = Blueprint("auth", __name__, url_prefix="/api/auth")


def tokens(user, sid, *, fresh, remaining=None):
    claims = {"role": user.role, "ver": user.auth_version, "sid": sid}
    access = create_access_token(identity=str(user.id), additional_claims=claims, fresh=fresh)
    refresh = create_refresh_token(identity=str(user.id), additional_claims=claims, expires_delta=remaining)
    return {"access_token": access, "refresh_token": refresh, "token_type": "Bearer",
            "expires_in": int(current_app.config["JWT_ACCESS_TOKEN_EXPIRES"].total_seconds()),
            "user": user_json(user)}


@bp.post("/register")
@limiter.limit("5 per minute")
def register():
    data = body(RegisterSchema())
    user = new_user(data)
    audit("user.register", user, actor=user.id, new={"role": "customer"})
    db.session.commit()
    return {"message": "Account created.", "user": user_json(user)}, 201


@bp.post("/login")
@limiter.limit("10 per minute")
def login():
    data = body(LoginSchema())
    user = db.session.scalar(select(User).where(User.email == data["email"]))
    if user is None:
        # Match the expensive operation on an existing account; do not reveal existence.
        bcrypt.check_password_hash(current_app.extensions["dummy_password_hash"], data["password"].encode("utf-8")[:72])
        valid = False
    else:
        valid = user.check_password(data["password"])
    if not valid or not user.is_active:
        raise Unauthorized("Invalid email or password.")
    user.last_login_at = utcnow()
    sid = str(uuid4())
    result = tokens(user, sid, fresh=True)
    refresh = decode_token(result["refresh_token"])
    db.session.add(AuthSession(id=sid, user_id=user.id, refresh_jti=refresh["jti"],
                               expires_at=token_expiry(refresh)))
    audit("auth.login", user, actor=user.id)
    db.session.commit()
    return result


@bp.post("/refresh")
@limiter.limit("30 per minute")
@jwt_required(refresh=True)
def refresh():
    old = get_jwt()
    result = tokens(current_user, old["sid"], fresh=False, remaining=token_expiry(old) - utcnow())
    replacement = decode_token(result["refresh_token"])
    # Compare-and-swap makes concurrent reuse fail across processes, without an in-memory lock.
    changed = db.session.execute(update(AuthSession).where(
        AuthSession.id == old["sid"], AuthSession.refresh_jti == old["jti"],
        AuthSession.revoked_at.is_(None), AuthSession.expires_at > utcnow()
    ).values(refresh_jti=replacement["jti"]))
    if changed.rowcount != 1:
        db.session.rollback()
        raise Unauthorized("Refresh token has already been used.")
    db.session.add(RevokedToken(jti=old["jti"], expires_at=token_expiry(old)))
    db.session.commit()
    return result


@bp.post("/logout")
@jwt_required(verify_type=False)
def logout():
    payload = get_jwt()
    changed = db.session.execute(update(AuthSession).where(
        AuthSession.id == payload["sid"], AuthSession.revoked_at.is_(None)
    ).values(revoked_at=utcnow()))
    if changed.rowcount:
        db.session.add(RevokedToken(jti=payload["jti"], expires_at=token_expiry(payload)))
        audit("auth.logout", current_user)
    db.session.commit()
    return {"message": "Logged out. This session's access and refresh tokens are revoked."}


@bp.get("/me")
@jwt_required()
def me():
    return {"user": user_json(current_user)}


@bp.patch("/me")
@jwt_required()
def update_profile():
    data = body(ProfileSchema())
    if "full_name" in data:
        set_name(current_user, data["full_name"])
    if "phone" in data:
        current_user.phone = data["phone"]
    audit("user.profile", current_user)
    db.session.commit()
    return {"user": user_json(current_user)}


@bp.post("/password")
@limiter.limit("5 per minute")
@jwt_required(fresh=True)
def password():
    data = body(PasswordSchema())
    if not current_user.check_password(data["current_password"]):
        raise Unauthorized("Invalid current password.")
    current_user.set_password(data["new_password"])
    audit("user.password", current_user)
    db.session.commit()
    return {"message": "Password changed. Log in again on all devices."}
