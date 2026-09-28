"""Evaluation endpoints authenticate and authorize independently of UI routing."""
from flask import Blueprint
from flask_jwt_extended import current_user
from marshmallow import fields, validate
from sqlalchemy import select
from werkzeug.exceptions import NotFound
from backend.db.models import Claim, ClaimEvaluation, Review
from backend.extensions import db
from backend.security import role_required
from backend.services.claim_evaluation import evaluate_claim, evaluation_json
from .common import audit, page
from .schemas import StrictSchema, body, claim_json

bp = Blueprint("evaluations", __name__, url_prefix="/api/claims")


class EvaluationSchema(StrictSchema):
    version = fields.Integer(required=True, strict=True, validate=validate.Range(min=1))
    idempotency_key = fields.String(required=True, validate=validate.Regexp(r"\A[A-Za-z0-9_-]{8,80}\Z"))


def accessible_claim(claim_id, lock=False):
    if not 1 <= claim_id <= 9223372036854775807:
        raise NotFound("Claim not found.")
    query = select(Claim).where(Claim.id == claim_id)
    if current_user.role == "customer":
        query = query.where(Claim.user_id == current_user.id)
    elif current_user.role == "employee":
        query = query.where(Claim.assigned_employee_id == current_user.id)
    elif current_user.role == "reviewer":
        query = query.where((Claim.assigned_reviewer_id == current_user.id) |
            (Claim.assigned_reviewer_id.is_(None) & (Claim.status == "manual_review")))
    claim = db.session.scalar(query.with_for_update() if lock else query)
    if claim is None:
        raise NotFound("Claim not found.")
    return claim


@bp.post("/<int:claim_id>/evaluate")
@role_required("employee")
def evaluate(claim_id):
    data = body(EvaluationSchema())
    claim = accessible_claim(claim_id, lock=True)
    evaluation, created = evaluate_claim(claim, **data)
    if created:
        audit("claim.evaluated", claim, new={"evaluation_id": evaluation.evaluation_id,
            "decision": evaluation.decision}, claim_id=claim.id)
        db.session.commit()
    return {"evaluation": evaluation_json(evaluation, internal=True), "claim": claim_json(claim)}, 201 if created else 200


@bp.get("/<int:claim_id>/evaluations")
@role_required("customer", "employee", "reviewer")
def evaluations(claim_id):
    accessible_claim(claim_id)
    return page(select(ClaimEvaluation).where(ClaimEvaluation.claim_id == claim_id)
        .order_by(ClaimEvaluation.id.desc()), lambda e: evaluation_json(e, internal=current_user.role != "customer"))


@bp.get("/<int:claim_id>/reviews")
@role_required("reviewer")
def reviews(claim_id):
    accessible_claim(claim_id)
    return page(select(Review).where(Review.claim_id == claim_id).order_by(Review.id.desc()),
        lambda r: {"review_id": r.review_id, "decision": r.decision, "comments": r.comments,
            "reviewer_id": r.reviewer_user_id, "override_applied": r.override_applied,
            "automated_decision": r.previous_decision, "evaluation_id": r.evaluation_id,
            "reviewed_at": r.reviewed_at.isoformat()})
