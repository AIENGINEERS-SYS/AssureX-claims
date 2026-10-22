"""Resource scope shared by HTTP handlers and business services."""
from sqlalchemy import false, select
from werkzeug.exceptions import Forbidden, NotFound
from backend.db.models import Claim
from backend.extensions import db


def claim_scope(actor, model=Claim):
    if not actor or not actor.is_active:
        return false()
    return {
        "customer": model.user_id == actor.id,
        "employee": model.assigned_employee_id == actor.id,
        "reviewer": model.assigned_reviewer_id == actor.id,
        "admin": True,
    }.get(actor.role, false())


def get_claim(claim_id, actor, *, lock=False, staff=False):
    if staff and actor.role not in {"employee", "reviewer", "admin"}:
        raise Forbidden("Staff access is required.")
    query = select(Claim).where(Claim.id == claim_id, claim_scope(actor))
    if lock:
        query = query.with_for_update()
    claim = db.session.scalar(query)
    if claim is None:
        raise NotFound("Claim not found.")
    return claim
