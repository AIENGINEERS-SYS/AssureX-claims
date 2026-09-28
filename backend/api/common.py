from flask import request
from flask_jwt_extended import current_user
from backend.db.models import AuditLog, User
from backend.extensions import db
from .schemas import PaginationSchema


def set_name(user, full_name):
    user.full_name = full_name
    first, _, last = full_name.partition(" ")
    user.first_name, user.last_name = first[:100], last[:100]


def new_user(data, role="customer"):
    user = User(email=data["email"], role=role)
    set_name(user, data["full_name"])
    user.set_password(data["password"])
    db.session.add(user)
    db.session.flush()
    return user


def audit(action, entity, *, actor=None, old=None, new=None, claim_id=None):
    db.session.add(AuditLog(user_id=actor if actor is not None else current_user.id,
        action=action, entity_type=entity.__tablename__, entity_id=str(entity.id),
        claim_id=claim_id, old_values=old, new_values=new,
        ip_address=request.remote_addr if request else None))


def page(statement, serializer):
    args = PaginationSchema().load(request.args.to_dict())
    result = db.paginate(statement, page=args["page"], per_page=args["per_page"], error_out=False)
    return {"items": [serializer(item) for item in result.items],
            "page": result.page, "per_page": result.per_page, "total": result.total}
