from flask import Blueprint
from flask_jwt_extended import current_user
from sqlalchemy import select
from werkzeug.exceptions import Conflict, NotFound
from backend.db.models import Claim, Review, RuleResult
from backend.extensions import db
from backend.security import role_required
from .common import audit, page
from .schemas import ReviewSchema, body, claim_json

bp = Blueprint("review", __name__, url_prefix="/api/review")


def reviewable(claim_id):
    claim = db.session.scalar(select(Claim).where(Claim.id == claim_id).with_for_update())
    if claim is None:
        raise NotFound("Claim not found.")
    if not claim.manual_review_required or claim.status != "manual_review":
        raise Conflict("Claim is not awaiting manual review.")
    return claim


@bp.get("/manual")
@role_required("reviewer")
def manual():
    return page(select(Claim).where(Claim.manual_review_required.is_(True),
                                   Claim.status == "manual_review").order_by(Claim.id), claim_json)


@bp.get("/<int:claim_id>/risk")
@role_required("reviewer")
def risk(claim_id):
    reviewable(claim_id)
    return page(select(RuleResult).where(RuleResult.claim_id == claim_id).order_by(RuleResult.id.desc()),
                lambda rule: {key: getattr(rule, key) for key in
                              ("rule_code", "rule_category", "result", "severity", "details", "policy_version")})


def record(claim_id, decision):
    data = body(ReviewSchema())
    claim = reviewable(claim_id)
    previous = claim.final_decision
    if decision in {"approve", "reject"}:
        claim.status = "approved" if decision == "approve" else "rejected"
        claim.final_decision = "likely_valid" if decision == "approve" else "likely_invalid"
        claim.manual_review_required = False
    db.session.add(Review(claim_id=claim.id, reviewer_user_id=current_user.id,
                           decision=decision, comments=data["notes"], previous_decision=previous))
    audit("review." + decision, claim, new={"status": claim.status}, claim_id=claim.id)
    db.session.commit()
    return {"claim": claim_json(claim)}


@bp.post("/<int:claim_id>/approve")
@role_required("reviewer")
def approve(claim_id):
    return record(claim_id, "approve")


@bp.post("/<int:claim_id>/reject")
@role_required("reviewer")
def reject(claim_id):
    return record(claim_id, "reject")


@bp.post("/<int:claim_id>/notes")
@role_required("reviewer")
def notes(claim_id):
    return record(claim_id, "manual_review_continue")
