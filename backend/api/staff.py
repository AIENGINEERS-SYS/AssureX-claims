"""Employee work on explicitly assigned claims, using shared scoped services."""
from flask import Blueprint
from flask_jwt_extended import current_user
from sqlalchemy import select
from backend.db.models import AuditLog
from backend.security import role_required
from backend.services.access import get_claim
from backend.services import assessments
from backend.services.document_service import document_json
from backend.services.evaluation import latest_evaluation, evaluation_json
from .common import page
from .schemas import claim_json

bp = Blueprint("employee", __name__, url_prefix="/api/employee")


@bp.get("/claims/<int:claim_id>")
@role_required("employee", "admin")
def details(claim_id):
    claim = get_claim(claim_id, current_user, staff=True)
    evaluation = latest_evaluation(claim.id)
    return {"claim": claim_json(claim),
        "documents": [document_json(document, include_text=True, include_fraud=True) for document in claim.documents],
        "evaluation": evaluation_json(evaluation) if evaluation else None,
        "assessment": assessments.public(assessments.latest(claim.id))}


@bp.get("/claims/<int:claim_id>/audit-history")
@role_required("employee", "admin")
def history(claim_id):
    get_claim(claim_id, current_user, staff=True)
    return page(select(AuditLog).where(AuditLog.claim_id == claim_id).order_by(AuditLog.id.desc()),
        lambda item: {"audit_id": item.audit_id, "action": item.action,
                      "recorded_at": item.created_at.isoformat(), "actor_id": item.user_id})


