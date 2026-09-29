"""Shared API helpers, including the immutable audit-event writer."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from flask import has_request_context, request
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


def _json_value(value):
    """Convert known values into safe JSON without introspecting arbitrary objects."""
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (UUID, Enum)):
        return str(value.value if isinstance(value, Enum) else value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _actor_id(actor):
    if actor is not None:
        return getattr(actor, "id", actor)
    # Registration and system events may deliberately have no JWT identity.
    return getattr(current_user, "id", None)


def request_audit_context():
    """Return bounded request metadata suitable for a durable audit record."""
    if not has_request_context():
        return {"ip_address": None, "user_agent": None}
    # Flask/ProxyFix owns the trusted-proxy decision; raw forwarding headers
    # are not trustworthy when copied directly from a client request.
    return {
        "ip_address": (request.remote_addr or "")[:45] or None,
        "user_agent": (request.user_agent.string or "")[:2048] or None,
    }


def audit(action, entity, *, actor=None, old=None, new=None, claim_id=None):
    """Append one immutable audit event in the current database transaction."""
    db.session.add(AuditLog(
        user_id=_actor_id(actor), action=action, entity_type=entity.__tablename__,
        entity_id=str(entity.id), claim_id=claim_id,
        old_values=_json_value(old), new_values=_json_value(new),
        **request_audit_context(),
    ))


def page(statement, serializer):
    args = PaginationSchema().load(request.args.to_dict())
    result = db.paginate(statement, page=args["page"], per_page=args["per_page"], error_out=False)
    return {"items": [serializer(item) for item in result.items],
            "page": result.page, "per_page": result.per_page, "total": result.total}
