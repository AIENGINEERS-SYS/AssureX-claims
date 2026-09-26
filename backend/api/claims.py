"""Example claim workflows with ownership, assignment and state checks."""
from flask import Blueprint, current_app, request, send_file
from flask_jwt_extended import current_user
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import BadRequest, Conflict, NotFound
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
@role_required("customer", "employee")
def upload(claim_id):
    if current_user.role == "customer":
        from .claim_schemas import ClaimUploadSchema
        from .documents import upload_to_claim
        from backend.services.claim_submission import owned_claim, workflow_json
        from backend.services.document_service import document_json
        data = ClaimUploadSchema().load(request.form.to_dict())
        if len(request.files.getlist("file")) != 1 or set(request.files) != {"file"}:
            from backend.services.document_errors import document_error
            raise document_error("unsupported_document", "Upload exactly one document per request.")
        claim = owned_claim(claim_id)
        document = upload_to_claim(claim, data, request.files["file"])
        return {"document": document_json(document, include_text=True), "claim": workflow_json(claim)}, 201
    from backend.services.claim_storage import storage, storage_key, total_limit, validate_file
    from backend.services.document_errors import DocumentError, document_error
    from backend.services.document_service import (document_json, duplicate_on_other_claim,
        process_document, stored_document_type)
    from .claim_schemas import DOCUMENT_TYPES
    claim = assigned_claim(claim_id, lock=True)
    if claim.status in {"approved", "rejected", "closed"}:
        raise Conflict("Documents cannot be added to a finalized claim.")
    if len(request.files.getlist("file")) != 1 or set(request.files) != {"file"}:
        raise document_error("unsupported_document", "Upload exactly one document per request.")
    kind = request.form.get("document_type", "other")
    if kind not in DOCUMENT_TYPES:
        raise document_error("unsupported_document", "Select a supported document type.")
    try:
        content, name, mime, suffix, digest = validate_file(request.files["file"])
    except DocumentError as exc:
        audit("document_rejected", claim, new={"reason": exc.error_code}, claim_id=claim.id)
        db.session.commit()
        raise
    if db.session.scalar(select(Document.id).where(Document.claim_id == claim.id,
            Document.file_hash == digest).limit(1)) is not None:
        audit("duplicate_detected", claim, new={"scope": "same_claim"}, claim_id=claim.id)
        db.session.commit()
        raise document_error("duplicate_document", "This document has already been uploaded to this claim.", 409)
    if len(claim.documents) >= current_app.config["MAX_DOCUMENTS_PER_CLAIM"]:
        audit("document_rejected", claim, new={"reason": "document_limit_reached"}, claim_id=claim.id)
        db.session.commit()
        raise document_error("document_limit_reached",
            f"A claim can contain at most {current_app.config['MAX_DOCUMENTS_PER_CLAIM']} documents.", 413)
    if sum(d.file_size for d in claim.documents) + len(content) > total_limit():
        audit("document_rejected", claim, new={"reason": "claim_upload_too_large"}, claim_id=claim.id)
        db.session.commit()
        raise document_error("claim_upload_too_large",
            f"Total claim uploads cannot exceed {current_app.config['MAX_CLAIM_UPLOAD_SIZE_MB']} MB.", 413)
    db.session.execute(update(Claim).where(Claim.id == claim.id).values(version=Claim.version + 1))
    db.session.refresh(claim)
    key = storage_key(claim.id, suffix)
    store = storage()
    document = Document(claim_id=claim.id, uploaded_by=current_user.id, document_type=stored_document_type(kind),
        original_filename=name, stored_filename=key.rsplit("/", 1)[-1], mime_type=mime, file_size=len(content),
        storage_path=key, file_hash=digest, cross_claim_duplicate=duplicate_on_other_claim(digest, claim.id))
    try:
        store.save(key, content, mime)
        db.session.add(document)
        db.session.flush()
        audit("document_uploaded", document, claim_id=claim.id)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        store.delete(key)
        if db.session.scalar(select(Document.id).where(Document.claim_id == claim.id,
                Document.file_hash == digest).limit(1)) is not None:
            audit("duplicate_detected", claim, new={"scope": "same_claim_race"}, claim_id=claim.id)
            db.session.commit()
            raise document_error("duplicate_document", "This document has already been uploaded to this claim.", 409)
        raise
    except Exception as exc:
        db.session.rollback()
        store.delete(key)
        current_app.logger.error("Document storage failed claim_id=%s error=%s", claim.id, type(exc).__name__)
        claim = db.session.get(Claim, claim.id)
        audit("document_rejected", claim, new={"reason": "storage_failure"}, claim_id=claim.id)
        db.session.commit()
        raise document_error("storage_failure", "The document could not be stored. Please retry.", 503) from None
    audit("ocr_started", document, claim_id=claim.id)
    action = process_document(document, content)
    audit(action, document, new={"status": document.ocr_status,
        "duration_ms": document.processing_duration_ms}, claim_id=claim.id)
    db.session.commit()
    return {"document": document_json(document, include_text=True, include_fraud=True) |
            {"filename": document.original_filename, "size": document.file_size}}, 201


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
