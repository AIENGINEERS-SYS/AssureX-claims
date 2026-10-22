"""Shared API helpers, including the immutable audit-event writer."""
from __future__ import annotations

from flask import request
from backend.db.models import User
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


from backend.services.audit import audit, request_audit_context, _json_value


def page(statement, serializer):
    args = PaginationSchema().load(request.args.to_dict())
    result = db.paginate(statement, page=args["page"], per_page=args["per_page"], error_out=False)
    return {"items": [serializer(item) for item in result.items],
            "page": result.page, "per_page": result.per_page, "total": result.total}
