from flask import Blueprint
from flask_jwt_extended import current_user
from sqlalchemy import select
from werkzeug.exceptions import Conflict, NotFound
from backend.db.models import AuditLog, Claim, Review, RuleResult
from backend.extensions import db
from backend.security import role_required
from backend.services.notifications import NotificationService
from .common import audit, page
from .schemas import OverrideSchema, ReviewSchema, body, claim_json

bp = Blueprint("review", __name__, url_prefix="/api/review")


def reviewable(claim_id):
    query = select(Claim).where(Claim.id == claim_id)
    if current_user.role != "admin":
        query = query.where((Claim.assigned_reviewer_id.is_(None)) |
            (Claim.assigned_reviewer_id == current_user.id))
    claim = db.session.scalar(query.with_for_update())
    if claim is None:
        raise NotFound("Claim not found.")
    if not claim.manual_review_required or claim.status != "manual_review":
        raise Conflict("Claim is not awaiting manual review.")
    return claim


@bp.get("/manual")
@role_required("reviewer")
def manual():
    query = select(Claim).where(Claim.manual_review_required.is_(True), Claim.status == "manual_review")
    if current_user.role != "admin":
        query = query.where((Claim.assigned_reviewer_id.is_(None)) |
            (Claim.assigned_reviewer_id == current_user.id))
    return page(query.order_by(Claim.id), claim_json)


@bp.get("/<int:claim_id>/risk")
@role_required("reviewer")
def risk(claim_id):
    reviewable(claim_id)
    return page(select(RuleResult).where(RuleResult.claim_id == claim_id).order_by(RuleResult.id.desc()),
                lambda rule: {key: getattr(rule, key) for key in
                              ("rule_code", "rule_category", "result", "severity", "details", "policy_version")})


def record(claim_id, decision, data=None, *, explicit_override=False):
    data = data or body(ReviewSchema())
    claim = reviewable(claim_id)
    previous = claim.final_decision
    human_decision = "likely_valid" if decision == "approve" else "likely_invalid" if decision == "reject" else None
    differs = bool(human_decision and previous and human_decision != previous)
    if differs and not explicit_override:
        raise Conflict("This action differs from the automated recommendation. Use the override action and provide a reason.")
    if decision in {"approve", "reject"}:
        claim.status = "approved" if decision == "approve" else "rejected"
        claim.final_decision = human_decision
        claim.manual_review_required = False
    db.session.add(Review(claim_id=claim.id, reviewer_user_id=current_user.id,
                           decision=decision, comments=data["notes"], previous_decision=previous,
                           override_applied=explicit_override,
                           override_reason=data.get("override_reason") if explicit_override else None))
    if decision == "approve":
        NotificationService().send_claim_approved(claim)
    elif decision == "reject":
        NotificationService().send_claim_rejected(claim, data.get("rejection_reason"))
    audit("review.override" if explicit_override else "review." + decision, claim,
          old={"automated_recommendation": previous},
          new={"status": claim.status, "human_decision": human_decision,
               "override_reason": data.get("override_reason") if explicit_override else None}, claim_id=claim.id)
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


@bp.post("/<int:claim_id>/override")
@role_required("reviewer")
def override(claim_id):
    data = body(OverrideSchema())
    return record(claim_id, data["decision"], data, explicit_override=True)


@bp.get("/<int:claim_id>/audit-history")
@role_required("reviewer")
def history(claim_id):
    claim = db.session.scalar(select(Claim).where(Claim.id == claim_id))
    if claim is None or (current_user.role != "admin" and claim.assigned_reviewer_id not in (None, current_user.id)):
        raise NotFound("Claim not found.")
    reviews = db.session.scalars(select(Review).where(Review.claim_id == claim.id)
        .order_by(Review.reviewed_at.desc(), Review.id.desc())).all()
    audits = db.session.scalars(select(AuditLog).where(AuditLog.claim_id == claim.id)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(200)).all()
    return {"reviews": [{"review_id": item.review_id, "decision": item.decision,
        "comments": item.comments, "override_applied": item.override_applied,
        "override_reason": item.override_reason, "previous_decision": item.previous_decision,
        "reviewer_user_id": item.reviewer_user_id, "reviewed_at": item.reviewed_at.isoformat()}
        for item in reviews], "audit": [{"audit_id": item.audit_id, "action": item.action,
        "entity_type": item.entity_type, "old_values": item.old_values, "new_values": item.new_values,
        "user_id": item.user_id, "created_at": item.created_at.isoformat()} for item in audits]}
