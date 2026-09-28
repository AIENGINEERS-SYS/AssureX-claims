from flask import Blueprint
from flask_jwt_extended import current_user, jwt_required
from sqlalchemy import func, select
from werkzeug.exceptions import BadRequest, NotFound
from backend.db.models import Claim, User
from backend.extensions import db
from backend.security import role_required
from .common import audit, new_user, page
from .schemas import AdminCreateSchema, AdminUpdateSchema, body, claim_json, user_json

bp = Blueprint("admin", __name__, url_prefix="/api/admin")


@bp.get("/users")
@role_required("admin")
def users():
    return page(select(User).order_by(User.id), user_json)


@bp.post("/users")
@role_required("admin")
@jwt_required(fresh=True)
def create_user():
    data = body(AdminCreateSchema())
    user = new_user(data, role=data["role"])
    audit("admin.user.create", user, new={"role": user.role})
    db.session.commit()
    return {"user": user_json(user)}, 201


def edit_user(user_id, data):
    # Serializes admin mutations in PostgreSQL; recheck the actor after acquiring locks.
    admins = db.session.scalars(select(User).where(User.role == "admin")
        .order_by(User.id).with_for_update().execution_options(populate_existing=True)).all()
    if not any(user.id == current_user.id and user.is_active for user in admins):
        raise BadRequest("Administrator access is no longer active.")
    user = db.session.get(User, user_id, populate_existing=True, with_for_update=True)
    if user is None:
        raise NotFound("User not found.")
    if user.id == current_user.id and (data.get("role", "admin") != "admin" or data.get("is_active") is False):
        raise BadRequest("You cannot demote or deactivate your own account.")
    previous = {"role": user.role, "is_active": user.is_active}
    for key, value in data.items():
        setattr(user, key, value)
    if previous != {"role": user.role, "is_active": user.is_active}:
        user.auth_version += 1
    audit("admin.user.update", user, old=previous, new=data)
    db.session.commit()
    return user


@bp.patch("/users/<int:user_id>")
@role_required("admin")
@jwt_required(fresh=True)
def update_user(user_id):
    return {"user": user_json(edit_user(user_id, body(AdminUpdateSchema())))}


@bp.delete("/users/<int:user_id>")
@role_required("admin")
@jwt_required(fresh=True)
def delete_user(user_id):
    edit_user(user_id, {"is_active": False})
    return {"message": "User deactivated and all their tokens invalidated."}


@bp.get("/claims")
@role_required("admin")
def claims():
    return page(select(Claim).order_by(Claim.id.desc()), claim_json)


@bp.get("/analytics")
@role_required("admin")
def analytics():
    return {"users": db.session.scalar(select(func.count(User.id))),
            "active_users": db.session.scalar(select(func.count(User.id)).where(User.is_active.is_(True))),
            "claims_by_status": dict(db.session.execute(select(Claim.status, func.count(Claim.id)).group_by(Claim.status)).all())}
