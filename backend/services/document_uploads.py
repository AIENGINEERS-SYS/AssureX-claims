"""Shared claim evidence upload, storage, locking, OCR and audit operations."""
from flask import current_app
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import Conflict, Forbidden
from backend.db.models import Claim, Document, utcnow
from backend.extensions import db
from backend.schemas.claims import DOCUMENT_TYPES
from .access import get_claim
from .audit import audit
from .claim_storage import storage, storage_key, total_limit, validate_file
from .claim_submission import lock_draft
from .document_errors import DocumentError, document_error
from .document_service import duplicate_on_other_claim, stored_document_type, start_attempt, processing_document, process_document


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



def _audit_rejection(claim, code, actor):
    db.session.rollback()
    claim = get_claim(claim.id, actor)
    audit("document_rejected", claim, new={"reason": code}, claim_id=claim.id, actor=actor)
    db.session.commit()


def upload_to_claim(claim, data, upload, *, actor):
    claim = get_claim(claim.id, actor, lock=True)
    if actor.role not in {'customer', 'employee', 'admin'}:
        raise Forbidden('This role cannot upload claim evidence.')
    if actor.role != 'customer' and claim.status in {'draft', 'approved', 'rejected', 'closed'}:
        raise Conflict('Documents cannot be added in this claim state.')
    if data.get('document_type') not in DOCUMENT_TYPES:
        raise document_error('unsupported_document', 'Select a supported document type.')
    try:
        content, name, mime, suffix, digest = validate_file(upload)
    except DocumentError as exc:
        _audit_rejection(claim, exc.error_code, actor)
        raise
    duplicate = db.session.scalar(select(Document.id).where(
        Document.claim_id == claim.id, Document.file_hash == digest).limit(1))
    if duplicate is not None:
        audit("duplicate_detected", claim, new={"scope": "same_claim"}, claim_id=claim.id, actor=actor)
        db.session.commit()
        raise document_error("duplicate_document", "This document has already been uploaded to this claim.", 409)
    if actor.role == "customer":
        _lock_review_claim(claim, data["version"])
    else:
        changed = db.session.execute(update(Claim).where(Claim.id == claim.id,
            Claim.status == claim.status, Claim.version == claim.version)
            .values(version=Claim.version + 1, updated_at=utcnow()),
            execution_options={"synchronize_session": False})
        if changed.rowcount != 1:
            raise Conflict("This claim changed. Refresh before uploading.")
        db.session.refresh(claim)
    if not claim.product_id:
        _audit_rejection(claim, "invalid_claim", actor)
        raise document_error("invalid_claim", "Select a product before uploading documents.")
    if len(claim.documents) >= current_app.config["MAX_DOCUMENTS_PER_CLAIM"]:
        _audit_rejection(claim, "document_limit_reached", actor)
        raise document_error("document_limit_reached",
            f"A claim can contain at most {current_app.config['MAX_DOCUMENTS_PER_CLAIM']} documents.", 413)
    if sum(item.file_size for item in claim.documents) + len(content) > total_limit():
        _audit_rejection(claim, "claim_upload_too_large", actor)
        raise document_error("claim_upload_too_large",
            f"Total claim uploads cannot exceed {current_app.config['MAX_CLAIM_UPLOAD_SIZE_MB']} MB.", 413)
    cross_claim = duplicate_on_other_claim(digest, claim.id)
    key = storage_key(claim.id, suffix)
    document = Document(claim=claim, uploaded_by=actor.id,
        document_type=stored_document_type(data["document_type"]), product_id=claim.product_id,
        warranty_id=claim.warranty_id, original_filename=name,
        stored_filename=key.rsplit("/", 1)[-1], mime_type=mime, file_size=len(content),
        storage_path=key, file_hash=digest, upload_status="stored", ocr_status="pending",
        review_status="pending", cross_claim_duplicate=cross_claim)
    store = storage()
    try:
        store.save(key, content, mime)
        db.session.add(document)
        db.session.flush()
        identity = (document.id, document.document_id, claim.id)
        start_attempt(document)
        audit("document_uploaded", document, claim_id=claim.id, actor=actor)
        if cross_claim:
            audit("duplicate_detected", document, new={"scope": "cross_claim"}, claim_id=claim.id, actor=actor)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        store.delete(key)
        if db.session.scalar(select(Document.id).where(Document.claim_id == claim.id,
                Document.file_hash == digest).limit(1)) is not None:
            audit("duplicate_detected", claim, new={"scope": "same_claim_race"}, claim_id=claim.id, actor=actor)
            db.session.commit()
            raise document_error("duplicate_document", "This document has already been uploaded to this claim.", 409)
        raise
    except Exception as exc:
        db.session.rollback()
        store.delete(key)
        current_app.logger.error("Document storage failed claim_id=%s error=%s", claim.id, type(exc).__name__)
        _audit_rejection(claim, "storage_failure", actor)
        raise document_error("storage_failure", "The document could not be stored. Please retry.", 503) from None

    document = processing_document(*identity)
    # OCR is a separate committed phase. A future worker can invoke this same service.
    audit("ocr_started", document, claim_id=claim.id, actor=actor)
    action = process_document(document, content)
    audit(action, document, new={"status": document.ocr_status,
        "duration_ms": document.processing_duration_ms}, claim_id=claim.id, actor=actor)
    current_app.logger.info("document_processed claim_id=%s document_id=%s ocr_status=%s duration_ms=%s",
        claim.id, document.id, document.ocr_status, document.processing_duration_ms)
    db.session.commit()
    return processing_document(*identity)
