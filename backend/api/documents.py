"""Secure claim-document, OCR and correction endpoints."""
from io import BytesIO
from flask import Blueprint, current_app, request, send_file
from flask_jwt_extended import current_user
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import Conflict, NotFound
from backend.db.models import Claim, Document, utcnow
from backend.extensions import db
from backend.security import role_required
from backend.services.claim_storage import storage, storage_key, total_limit, validate_file
from backend.services.claim_submission import customer_only, lock_draft, owned_claim, workflow_json
from backend.services.document_errors import DocumentError, document_error
from backend.services.document_service import (document_json, duplicate_on_other_claim, process_document,
    review_document, stored_document_type)
from .claim_schemas import DocumentMutationSchema, OCRReviewSchema
from .common import audit
from .schemas import body

bp = Blueprint("documents", __name__, url_prefix="/api")


def _one_file():
    if len(request.files.getlist("file")) != 1 or set(request.files) != {"file"}:
        raise document_error("unsupported_document", "Upload exactly one document per request.")
    return request.files["file"]


def _audit_rejection(claim, code):
    db.session.rollback()
    claim = db.session.get(Claim, claim.id)
    audit("document_rejected", claim, new={"reason": code}, claim_id=claim.id)
    db.session.commit()


def upload_to_claim(claim, data, upload):
    try:
        content, name, mime, suffix, digest = validate_file(upload)
    except DocumentError as exc:
        _audit_rejection(claim, exc.error_code)
        raise
    duplicate = db.session.scalar(select(Document.id).where(
        Document.claim_id == claim.id, Document.file_hash == digest).limit(1))
    if duplicate is not None:
        audit("duplicate_detected", claim, new={"scope": "same_claim"}, claim_id=claim.id)
        db.session.commit()
        raise document_error("duplicate_document", "This document has already been uploaded to this claim.", 409)
    lock_draft(claim, data["version"])
    if not claim.product_id:
        _audit_rejection(claim, "invalid_claim")
        raise document_error("invalid_claim", "Select a product before uploading documents.")
    if len(claim.documents) >= current_app.config["MAX_DOCUMENTS_PER_CLAIM"]:
        _audit_rejection(claim, "document_limit_reached")
        raise document_error("document_limit_reached",
            f"A claim can contain at most {current_app.config['MAX_DOCUMENTS_PER_CLAIM']} documents.", 413)
    if sum(item.file_size for item in claim.documents) + len(content) > total_limit():
        _audit_rejection(claim, "claim_upload_too_large")
        raise document_error("claim_upload_too_large",
            f"Total claim uploads cannot exceed {current_app.config['MAX_CLAIM_UPLOAD_SIZE_MB']} MB.", 413)
    cross_claim = duplicate_on_other_claim(digest, claim.id)
    key = storage_key(claim.id, suffix)
    document = Document(claim=claim, uploaded_by=current_user.id,
        document_type=stored_document_type(data["document_type"]), original_filename=name,
        stored_filename=key.rsplit("/", 1)[-1], mime_type=mime, file_size=len(content),
        storage_path=key, file_hash=digest, upload_status="stored", ocr_status="pending",
        review_status="pending", cross_claim_duplicate=cross_claim)
    store = storage()
    try:
        store.save(key, content, mime)
        db.session.add(document)
        db.session.flush()
        audit("document_uploaded", document, claim_id=claim.id)
        if cross_claim:
            audit("duplicate_detected", document, new={"scope": "cross_claim"}, claim_id=claim.id)
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
        _audit_rejection(claim, "storage_failure")
        raise document_error("storage_failure", "The document could not be stored. Please retry.", 503) from None

    # OCR is a separate committed phase. A future worker can invoke this same service.
    audit("ocr_started", document, claim_id=claim.id)
    action = process_document(document, content)
    audit(action, document, new={"status": document.ocr_status,
        "duration_ms": document.processing_duration_ms}, claim_id=claim.id)
    current_app.logger.info("document_processed claim_id=%s document_id=%s ocr_status=%s duration_ms=%s",
        claim.id, document.id, document.ocr_status, document.processing_duration_ms)
    db.session.commit()
    return document


def accessible_document(document_id):
    statement = select(Document).join(Claim, Document.claim_id == Claim.id).where(Document.id == document_id)
    if current_user.role == "customer":
        statement = statement.where(Claim.user_id == current_user.id)
    elif current_user.role == "employee":
        statement = statement.where(Claim.assigned_employee_id == current_user.id)
    elif current_user.role == "reviewer":
        statement = statement.where(Claim.manual_review_required.is_(True))
    document = db.session.scalar(statement)
    if document is None:
        raise NotFound("Document not found.")
    return document


@bp.get("/claims/<identifier>/documents")
@role_required("customer", "employee", "reviewer")
def list_documents(identifier):
    if current_user.role == "customer":
        claim = owned_claim(identifier)
    else:
        numeric = isinstance(identifier, int) or identifier.isdecimal()
        field, value = (Claim.id, int(identifier)) if numeric else (Claim.claim_id, identifier)
        statement = select(Claim).where(field == value)
        if current_user.role == "employee":
            statement = statement.where(Claim.assigned_employee_id == current_user.id)
        elif current_user.role == "reviewer":
            statement = statement.where(Claim.manual_review_required.is_(True))
        claim = db.session.scalar(statement)
        if claim is None:
            raise NotFound("Claim not found.")
    include_fraud = current_user.role in {"employee", "reviewer", "admin"}
    return {"items": [document_json(item, include_fraud=include_fraud) for item in claim.documents],
            "total": len(claim.documents)}


@bp.get("/documents/<int:document_id>")
@role_required("customer", "employee", "reviewer")
def document_details(document_id):
    return {"document": document_json(accessible_document(document_id),
        include_fraud=current_user.role in {"employee", "reviewer", "admin"})}


@bp.get("/documents/<int:document_id>/ocr")
@role_required("customer", "employee", "reviewer")
def document_ocr(document_id):
    return {"document": document_json(accessible_document(document_id), include_text=True,
        include_fraud=current_user.role in {"employee", "reviewer", "admin"})}


@bp.get("/documents/<int:document_id>/content")
@role_required("customer", "employee", "reviewer")
def document_content(document_id):
    document = accessible_document(document_id)
    return send_file(BytesIO(storage().open(document.storage_path)), mimetype=document.mime_type,
        as_attachment=True, download_name=document.original_filename)


def _lock_review_claim(claim, version):
    if claim.status == "draft":
        lock_draft(claim, version)
        return
    result = db.session.execute(update(Claim).where(Claim.id == claim.id,
        Claim.status == "additional_information_required", Claim.version == version)
        .values(version=Claim.version + 1, updated_at=utcnow()),
        execution_options={"synchronize_session": False})
    if result.rowcount != 1:
        raise Conflict("This claim changed or cannot be edited. Reload it before continuing.")
    db.session.refresh(claim)


@bp.patch("/documents/<int:document_id>/ocr-review")
@customer_only
def review_ocr(document_id):
    data = body(OCRReviewSchema())
    document = accessible_document(document_id)
    claim = document.claim
    if document.ocr_status in {"pending", "processing", "failed"}:
        raise Conflict("OCR must finish successfully before extracted information can be confirmed.")
    _lock_review_claim(claim, data.pop("version"))
    data.pop("confirm")
    corrected = review_document(document, data, current_user.id)
    audit("ocr_reviewed", document, new={"corrected": corrected}, claim_id=claim.id)
    if corrected:
        audit("ocr_corrected", document, new={"fields": sorted(data)}, claim_id=claim.id)
    db.session.commit()
    return {"document": document_json(document, include_text=True), "claim": workflow_json(claim)}


@bp.post("/documents/<int:document_id>/ocr/retry")
@customer_only
def retry_ocr(document_id):
    data = body(DocumentMutationSchema())
    document = accessible_document(document_id)
    claim = document.claim
    if document.ocr_status not in {"failed", "completed_with_warnings"}:
        raise Conflict("Only failed or warning OCR results can be retried.")
    _lock_review_claim(claim, data["version"])
    content = storage().open(document.storage_path)
    audit("ocr_started", document, new={"retry": True}, claim_id=claim.id)
    action = process_document(document, content)
    audit(action, document, new={"retry": True, "status": document.ocr_status}, claim_id=claim.id)
    db.session.commit()
    return {"document": document_json(document, include_text=True), "claim": workflow_json(claim)}


@bp.delete("/documents/<int:document_id>")
@customer_only
def delete_document(document_id):
    data = body(DocumentMutationSchema())
    document = accessible_document(document_id)
    claim = document.claim
    lock_draft(claim, data["version"])
    key = document.storage_path
    audit("document_deleted", document, claim_id=claim.id)
    db.session.delete(document)
    db.session.commit()
    try:
        storage().delete(key)
    except Exception:
        current_app.logger.error("Orphan document cleanup required storage_key=%s", key)
    return {"claim": workflow_json(claim)}
