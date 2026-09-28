"""JWT-protected dashboard endpoints and reviewer workflow actions."""
from datetime import timedelta
from flask import Blueprint, current_app, request
from flask_jwt_extended import current_user
from sqlalchemy import func, select
from werkzeug.exceptions import BadRequest, NotFound
from backend.db.models import Claim, Document, DuplicateInvestigation, Notification, Review, User, utcnow
from backend.extensions import db, limiter
from backend.security import role_required
from backend.services.dashboard import (WINDOWS, admin_dashboard, customer_dashboard, duplicate_pairs,
    model_history, review_scope, reviewer_dashboard, trends)
from backend.services.dashboard_notifications import notify, serialize
from .claim_schemas import DOCUMENT_TYPES
from .common import audit

bp = Blueprint("dashboard", __name__, url_prefix="/api/dashboard")


@bp.after_request
def log_read(response):
    if request.method == "GET" and response.status_code < 400:
        current_app.logger.info("dashboard_access role=%s actor_id=%s route=%s",
            current_user.role, current_user.id, request.endpoint)
    return response


def pagination():
    try:
        page, per_page = int(request.args.get("page", "1")), int(request.args.get("per_page", "10"))
    except ValueError as exc:
        raise BadRequest("Pagination values must be whole numbers.") from exc
    if not 1 <= page <= 100000 or not 1 <= per_page <= 50:
        raise BadRequest("Use page >= 1 and per_page between 1 and 50.")
    return page, per_page


@bp.get("/customer")
@role_required("customer")
@limiter.limit("60 per minute")
def customer():
    user_id = current_user.id
    if current_user.role == "admin":
        user_id = request.args.get("user_id", type=int)
        if not user_id or db.session.scalar(select(User.id).where(
                User.id == user_id, User.role == "customer")) is None:
            raise BadRequest("Administrators must provide a valid customer user_id.")
    return customer_dashboard(user_id, *pagination())


@bp.get("/reviewer")
@role_required("reviewer")
@limiter.limit("60 per minute")
def reviewer():
    search = request.args.get("search", "").strip()
    if len(search) > 80:
        raise BadRequest("Search must contain at most 80 characters.")
    return reviewer_dashboard(current_user.id, current_user.role == "admin", *pagination(), search)


@bp.get("/admin")
@role_required("admin")
@limiter.limit("60 per minute")
def admin():
    return admin_dashboard()


@bp.get("/analytics")
@role_required("admin")
@limiter.limit("60 per minute")
def analytics():
    return admin_dashboard()


@bp.get("/analytics/models")
@role_required("admin")
def models():
    return model_history(*pagination())


@bp.get("/trends")
@role_required("customer", "reviewer")
@limiter.limit("60 per minute")
def trends_endpoint():
    window, interval = request.args.get("window", "30d"), request.args.get("interval", "day")
    if window not in WINDOWS or interval not in {"day", "week", "month"}:
        raise BadRequest("Use window 7d, 30d, 90d, 12m and interval day, week or month.")
    if current_user.role == "reviewer":
        since = utcnow() - timedelta(days=WINDOWS[window])
        rows = db.session.execute(select(func.date(Review.reviewed_at), Review.decision, func.count()).where(
            Review.reviewer_user_id == current_user.id, Review.reviewed_at >= since).
            group_by(func.date(Review.reviewed_at), Review.decision))
        return {"window": window, "series": [{"date": str(day), "decision": decision, "count": n}
            for day, decision, n in rows]}
    role = "admin" if current_user.role == "admin" else "customer"
    return trends(window, role, None if role == "admin" else current_user.id, interval)


@bp.get("/notifications")
@role_required("customer", "employee", "reviewer")
def notifications():
    query = select(Notification).where(Notification.user_id == current_user.id)
    if request.args.get("unread") == "true":
        query = query.where(Notification.is_read.is_(False))
    page, per_page = pagination()
    result = db.paginate(query.order_by(Notification.created_at.desc(), Notification.id.desc()),
        page=page, per_page=per_page, error_out=False)
    return {"items": [serialize(item) for item in result.items], "page": page,
        "per_page": per_page, "total": result.total, "pages": result.pages}


@bp.patch("/notifications/<int:notification_id>/read")
@role_required("customer", "employee", "reviewer")
def mark_read(notification_id):
    item = db.session.scalar(select(Notification).where(Notification.id == notification_id,
        Notification.user_id == current_user.id).with_for_update())
    if item is None:
        raise NotFound("Notification not found.")
    if not item.is_read:
        item.is_read, item.read_at = True, utcnow()
        audit("notification.read", item)
        db.session.commit()
    return {"notification": serialize(item)}


def visible_claim(claim_id, lock=False, include_info=False):
    scope = review_scope(current_user.id, current_user.role == "admin")
    if include_info:
        info = Claim.status == "additional_information_required"
        if current_user.role != "admin":
            info = info & (Claim.assigned_reviewer_id == current_user.id)
        scope = scope | info
    query = select(Claim).where(Claim.id == claim_id, scope)
    if lock:
        query = query.with_for_update()
    claim = db.session.scalar(query)
    if claim is None:
        raise NotFound("Review claim not found.")
    return claim


@bp.get("/reviewer/claims/<int:claim_id>")
@role_required("reviewer")
def review_details(claim_id):
    claim = visible_claim(claim_id, include_info=True)
    from backend.services.claim_evaluation import latest_evaluation, evaluation_json
    evaluation = latest_evaluation(claim.id)
    documents = db.session.execute(select(Document.id, Document.document_type, Document.ocr_status,
        Document.review_status).where(Document.claim_id == claim.id)).all()
    return {"evaluation": evaluation_json(evaluation, internal=True) if evaluation else None,
        "claim": {"id": claim.id, "claim_id": claim.claim_id, "status": claim.status, "version": claim.version,
        "customer": claim.user.full_name, "product": claim.product.name if claim.product else None,
        "description": claim.fault_description, "fault_type": claim.fault_type,
        "submitted_at": claim.submitted_at.isoformat() if claim.submitted_at else None,
        "assigned_reviewer_id": claim.assigned_reviewer_id,
        "documents": [{"id": r.id, "type": r.document_type, "ocr_status": r.ocr_status,
            "review_status": r.review_status} for r in documents]}}


@bp.patch("/reviewer/claims/<int:claim_id>/assignment")
@role_required("reviewer")
def assign(claim_id):
    claim = visible_claim(claim_id, lock=True)
    if current_user.role == "admin":
        data = request.get_json()
        if not isinstance(data, dict) or set(data) - {"reviewer_id", "version"}:
            raise BadRequest("Provide a reviewer ID and optional claim version.")
        reviewer_id = data.get("reviewer_id")
        if type(reviewer_id) is not int or db.session.scalar(select(User.id).where(User.id == reviewer_id,
                User.role == "reviewer", User.is_active.is_(True))) is None:
            raise BadRequest("Select an active reviewer.")
    else:
        reviewer_id = current_user.id
    old = claim.assigned_reviewer_id
    claim.assigned_reviewer_id = reviewer_id
    audit("review.assign", claim, old={"reviewer_id": old}, new={"reviewer_id": reviewer_id}, claim_id=claim.id)
    db.session.commit()
    return {"claim_id": claim.id, "assigned_reviewer_id": reviewer_id}


@bp.post("/reviewer/claims/<int:claim_id>/request-documents")
@role_required("reviewer")
def request_documents(claim_id):
    claim = visible_claim(claim_id, lock=True)
    data = request.get_json()
    if not isinstance(data, dict) or set(data) - {"document_types", "version"}:
        raise BadRequest("Provide document types and optional claim version.")
    if "version" in data and (type(data["version"]) is not int or data["version"] != claim.version):
        from werkzeug.exceptions import Conflict
        raise Conflict("The claim changed. Refresh before reviewing.")
    types = data.get("document_types")
    if not isinstance(types, list) or not types or any(type(t) is not str or t not in DOCUMENT_TYPES for t in types):
        raise BadRequest("Select at least one supported document type.")
    types = list(dict.fromkeys(types))
    claim.status, claim.manual_review_required = "additional_information_required", False
    if current_user.role == "reviewer":
        claim.assigned_reviewer_id = current_user.id
    from backend.services.claim_evaluation import latest_evaluation
    evaluation = latest_evaluation(claim.id)
    db.session.add(Review(claim_id=claim.id, reviewer_user_id=current_user.id,
        decision="request_information", comments="Requested documents: " + ", ".join(types),
        previous_decision=claim.final_decision, evaluation_id=evaluation.id if evaluation else None))
    notify(claim.user_id, "document_request", "More documents needed",
        f"Please add {', '.join(types)} to claim {claim.claim_id}.", claim_id=claim.id)
    audit("review.documents_requested", claim, new={"document_types": types}, claim_id=claim.id)
    db.session.commit()
    return {"claim_id": claim.id, "status": claim.status, "requested": types}


@bp.post("/reviewer/claims/<int:claim_id>/remind")
@role_required("reviewer")
@limiter.limit("10 per hour")
def remind(claim_id):
    claim = visible_claim(claim_id, include_info=True)
    if claim.status != "additional_information_required":
        raise BadRequest("This claim is not awaiting information.")
    notify(claim.user_id, "reviewer_reminder", "Claim information reminder",
        f"Please provide the requested information for claim {claim.claim_id}.", claim_id=claim.id)
    audit("review.reminder", claim, claim_id=claim.id)
    db.session.commit()
    return {"message": "Reminder sent."}


@bp.post("/reviewer/claims/<int:claim_id>/resume")
@role_required("reviewer")
def resume(claim_id):
    claim = visible_claim(claim_id, lock=True, include_info=True)
    if claim.status != "additional_information_required":
        raise BadRequest("This claim is not awaiting information.")
    claim.status, claim.manual_review_required = "manual_review", True
    audit("review.resumed", claim, new={"status": claim.status}, claim_id=claim.id)
    db.session.commit()
    return {"claim_id": claim.id, "status": claim.status}


@bp.post("/reviewer/duplicates/decision")
@role_required("reviewer")
def decide_duplicate():
    data = request.get_json()
    if not isinstance(data, dict) or set(data) - {"claim_id", "matching_claim_id", "file_hash", "status"}:
        raise BadRequest("Provide claim IDs, file hash and decision only.")
    left, right, digest, status = (data.get(k) for k in
        ("claim_id", "matching_claim_id", "file_hash", "status"))
    if (type(left) is not int or type(right) is not int or left == right or
            not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest) or
            not isinstance(status, str) or status not in {"confirmed", "false_positive"}):
        raise BadRequest("Provide two claim IDs, an exact file hash and a valid decision.")
    first, second = sorted((left, right))
    if current_user.role != "admin" and not db.session.scalar(select(Claim.id).where(
            Claim.id.in_((first, second)), review_scope(current_user.id)).limit(1)):
        raise NotFound("Duplicate warning not found.")
    pairs = duplicate_pairs()
    if db.session.scalar(select(pairs.c.claim_id).where(pairs.c.claim_id == first,
            pairs.c.matching_claim_id == second, pairs.c.file_hash == digest)) is None:
        raise NotFound("Duplicate warning not found.")
    decision = db.session.scalar(select(DuplicateInvestigation).where(
        DuplicateInvestigation.claim_id == first, DuplicateInvestigation.matching_claim_id == second,
        DuplicateInvestigation.file_hash == digest).with_for_update())
    if decision is None:
        decision = DuplicateInvestigation(claim_id=first, matching_claim_id=second,
            file_hash=digest, status=status, reviewer_user_id=current_user.id)
        db.session.add(decision)
    else:
        decision.status, decision.reviewer_user_id, decision.decided_at = status, current_user.id, utcnow()
    db.session.flush()
    audit("duplicate.decision", decision, new={"status": status}, claim_id=first)
    db.session.commit()
    return {"claim_id": first, "matching_claim_id": second, "status": status}
