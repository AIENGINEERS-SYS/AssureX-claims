"""Example claim workflows with ownership, assignment and state checks."""
from flask import Blueprint, request, send_file
from flask_jwt_extended import current_user
from sqlalchemy import select, update
from werkzeug.exceptions import BadRequest, Conflict, NotFound, RequestEntityTooLarge
from backend.db.models import Claim, Document, User
from backend.extensions import db
from backend.security import role_required
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
@role_required("customer")
def mine():
    from backend.services.claim_submission import workflow_json
    from sqlalchemy.orm import selectinload
    from backend.db.models import Product
    return page(select(Claim).where(Claim.user_id == current_user.id).options(
        selectinload(Claim.documents), selectinload(Claim.product).selectinload(Product.warranties)
    ).order_by(Claim.id.desc()), lambda c: claim_json(c) | workflow_json(c))


@bp.get("/assigned")
@role_required("employee")
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
    claim.assigned_employee_id = employee.id
    audit("claim.assign", claim, new=data, claim_id=claim.id)
    db.session.commit()
    return {"claim": claim_json(claim)}


@bp.patch("/<int:claim_id>/status")
@role_required("employee")
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
    claim.manual_review_required = claim.status == "manual_review"
    audit("claim.status", claim, old={"status": previous}, new=data, claim_id=claim.id)
    db.session.commit()
    return {"claim": claim_json(claim)}


@bp.post("/<int:claim_id>/documents")
@role_required("employee")
def upload(claim_id):
    from backend.services.claim_storage import TOTAL_LIMIT, storage, storage_key, validate_file
    claim = assigned_claim(claim_id, lock=True)
    db.session.execute(update(Claim).where(Claim.id == claim.id).values(version=Claim.version + 1))
    db.session.refresh(claim)
    if claim.status in {"approved", "rejected", "closed"}:
        raise Conflict("Documents cannot be added to a finalized claim.")
    content, name, mime, suffix, digest = validate_file(request.files.get("file"))
    if sum(d.file_size for d in claim.documents) + len(content) > TOTAL_LIMIT:
        raise RequestEntityTooLarge("Total upload size cannot exceed 50 MB.")
    key = storage_key(claim.id, suffix)
    store = storage()
    document = Document(claim_id=claim.id, uploaded_by=current_user.id, document_type="other",
        original_filename=name, stored_filename=key.rsplit("/", 1)[-1], mime_type=mime, file_size=len(content),
        storage_path=key, file_hash=digest)
    try:
        store.put(key, content, mime)
        db.session.add(document)
        db.session.flush()
        audit("document.upload", document, claim_id=claim.id)
        db.session.commit()
    except Exception:
        db.session.rollback()
        store.delete(key)
        raise
    return {"document": {"id": document.id, "filename": document.original_filename,
                         "size": document.file_size}}, 201


@bp.get("/<int:claim_id>/documents/<int:document_id>")
@role_required("customer", "employee", "reviewer")
def download(claim_id, document_id):
    query = select(Claim).where(Claim.id == claim_id)
    if current_user.role == "customer":
        query = query.where(Claim.user_id == current_user.id)
    elif current_user.role == "employee":
        query = query.where(Claim.assigned_employee_id == current_user.id)
    elif current_user.role == "reviewer":
        query = query.where(Claim.manual_review_required.is_(True))
    if db.session.scalar(query) is None:
        raise NotFound("Claim not found.")
    document = db.session.scalar(select(Document).where(Document.id == document_id, Document.claim_id == claim_id))
    if document is None:
        raise NotFound("Document not found.")
    from io import BytesIO
    from backend.services.claim_storage import storage
    return send_file(BytesIO(storage().read(document.storage_path)), mimetype="application/octet-stream", as_attachment=True,
                     download_name=document.original_filename)
