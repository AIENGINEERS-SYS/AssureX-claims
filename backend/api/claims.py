"""Example claim workflows with ownership, assignment and state checks."""
from flask import Blueprint, request, send_file
from flask_jwt_extended import current_user
from sqlalchemy import select
from werkzeug.exceptions import BadRequest, Conflict, NotFound
from backend.db.models import Claim, Document, User
from backend.extensions import db
from backend.security import role_required
from backend.services.notifications import NotificationService
from .common import audit, page
from .schemas import AssignmentSchema, StatusSchema, body, claim_json

bp = Blueprint("claims", __name__, url_prefix="/api/claims")


def assigned_claim(claim_id, *, lock=False):
    query = select(Claim).where(Claim.id == claim_id)
    if current_user.role != "admin":
        query = query.where(Claim.assigned_employee_id == current_user.id)
    if lock:
        query = query.with_for_update()
    claim = db.session.scalar(query)
    if claim is None:
        raise NotFound("Claim not found.")
    return claim


@bp.get("/my")
@role_required("customer", "admin")
def mine():
    from backend.services.claim_submission import workflow_json
    from sqlalchemy.orm import selectinload
    from backend.db.models import Product
    return page(select(Claim).where(Claim.user_id == current_user.id).options(
        selectinload(Claim.documents), selectinload(Claim.product).selectinload(Product.warranties)
    ).order_by(Claim.id.desc()), lambda c: workflow_json(c) | {"user_id": c.user_id})


@bp.get("/assigned")
@role_required("employee", "admin")
def assigned():
    query = select(Claim)
    if current_user.role != "admin":
        query = query.where(Claim.assigned_employee_id == current_user.id)
    return page(query.order_by(Claim.id.desc()), claim_json)


@bp.patch("/<int:claim_id>/assignment")
@role_required("admin")
def assign(claim_id):
    data = body(AssignmentSchema())
    claim = assigned_claim(claim_id, lock=True)
    employee = db.session.get(User, data["employee_id"])
    if employee is None or not employee.is_active or employee.role != "employee":
        raise BadRequest("Select an active employee.")
    previous = claim.assigned_employee_id
    claim.assigned_employee_id = employee.id
    audit("claim.assign", claim, old={"employee_id": previous}, new=data, claim_id=claim.id)
    db.session.commit()
    return {"claim": claim_json(claim)}


@bp.patch("/<int:claim_id>/status")
@role_required("employee", "admin")
def status(claim_id):
    data = body(StatusSchema())
    claim = assigned_claim(claim_id, lock=True)
    transitions = {
        "submitted": {"under_evaluation", "manual_review"},
        "under_evaluation": {"additional_information_required", "manual_review"},
        "additional_information_required": {"under_evaluation", "manual_review"},
    }
    if data["status"] not in transitions.get(claim.status, set()):
        raise Conflict("This processing status transition is not allowed.")
    previous = claim.status
    claim.status = data["status"]
    if claim.status == "additional_information_required":
        from backend.db.models import utcnow
        claim.information_request = {"document_types": data.get("document_types", []),
            "requested_at": utcnow().isoformat(), "existing_document_ids": [document.id for document in claim.documents],
            "message": data.get("reason", "Your service center requested additional evidence.")}
    claim.manual_review_required = claim.status == "manual_review"
    audit("claim.status_changed", claim, old={"status": previous},
          new={**data, "source": "employee"}, claim_id=claim.id)
    if claim.status == "manual_review":
        NotificationService().send_review_notification(claim)
    elif claim.status == "additional_information_required":
        NotificationService().send_information_requested(claim, data.get("document_types"), requester="Your service center")
    db.session.commit()
    return {"claim": claim_json(claim)}


@bp.post("/<int:claim_id>/documents")
@role_required("customer", "employee", "admin")
def upload(claim_id):
    from .claim_schemas import ClaimUploadSchema
    from backend.services.document_uploads import upload_to_claim
    from backend.services.claim_submission import owned_claim, workflow_json
    from backend.services.document_service import document_json
    if len(request.files.getlist("file")) != 1 or set(request.files) != {"file"}:
        from backend.services.document_errors import document_error
        raise document_error("unsupported_document", "Upload exactly one document per request.")
    if current_user.role == "customer":
        data = ClaimUploadSchema().load(request.form.to_dict())
        claim = owned_claim(claim_id)
    else:
        claim = assigned_claim(claim_id, lock=True)
        data = {"document_type": request.form.get("document_type", "other"), "version": claim.version}
    document = upload_to_claim(claim, data, request.files["file"], actor=current_user)
    if current_user.role == "customer":
        return {"document": document_json(document, include_text=True), "claim": workflow_json(claim)}, 201
    return {"document": document_json(document, include_text=True, include_fraud=True) |
            {"filename": document.original_filename, "size": document.file_size}}, 201


@bp.get("/<int:claim_id>/documents/<int:document_id>")
@role_required("customer", "employee", "reviewer", "admin")
def download(claim_id, document_id):
    query = select(Claim).where(Claim.id == claim_id)
    if current_user.role == "customer":
        query = query.where(Claim.user_id == current_user.id)
    elif current_user.role == "employee":
        query = query.where(Claim.assigned_employee_id == current_user.id)
    elif current_user.role == "reviewer":
        query = query.where(Claim.assigned_reviewer_id == current_user.id)
    if db.session.scalar(query) is None:
        raise NotFound("Claim not found.")
    document = db.session.scalar(select(Document).where(Document.id == document_id, Document.claim_id == claim_id))
    if document is None:
        raise NotFound("Document not found.")
    from io import BytesIO
    from backend.services.claim_storage import storage
    return send_file(BytesIO(storage().read(document.storage_path)), mimetype="application/octet-stream", as_attachment=True,
                     download_name=document.original_filename)
