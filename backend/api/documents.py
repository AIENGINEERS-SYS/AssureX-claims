"""Secure claim-document, OCR and correction endpoints."""
from io import BytesIO
from flask import Blueprint, current_app, request, send_file
from flask_jwt_extended import current_user
from sqlalchemy import select, update
from werkzeug.exceptions import Conflict, NotFound
from backend.db.models import Claim, Document, utcnow
from backend.extensions import db
from backend.security import role_required
from backend.services.claim_storage import storage
from backend.services.claim_submission import customer_only, lock_draft, owned_claim, workflow_json
from backend.services.document_errors import document_error
from backend.services.document_service import (document_json, duplicate_on_other_claim, process_document,
    review_document, stored_document_type, extraction, confirmation, start_attempt, can_retry, processing_document)
from .claim_schemas import DocumentMutationSchema, OCRReviewSchema
from .common import audit
from .schemas import body

bp = Blueprint("documents", __name__, url_prefix="/api")


def _one_file():
    if len(request.files.getlist("file")) != 1 or set(request.files) != {"file"}:
        raise document_error("unsupported_document", "Upload exactly one document per request.")
    return request.files["file"]


from backend.services.document_uploads import upload_to_claim, _lock_review_claim


def accessible_document(document_id):
    statement = select(Document).join(Claim, Document.claim_id == Claim.id).where(Document.id == document_id)
    from backend.services.access import claim_scope
    statement = statement.where(claim_scope(current_user))
    document = db.session.scalar(statement)
    if document is None:
        raise NotFound("Document not found.")
    return document


@bp.get("/claims/<identifier>/documents")
@role_required("customer", "employee", "reviewer", "admin")
def list_documents(identifier):
    if current_user.role == "customer":
        claim = owned_claim(identifier)
    else:
        numeric = isinstance(identifier, int) or identifier.isdecimal()
        field, value = (Claim.id, int(identifier)) if numeric else (Claim.claim_id, identifier)
        statement = select(Claim).where(field == value)
        from backend.services.access import claim_scope
        statement = statement.where(claim_scope(current_user))
        claim = db.session.scalar(statement)
        if claim is None:
            raise NotFound("Claim not found.")
    include_fraud = current_user.role in {"employee", "reviewer", "admin"}
    return {"items": [document_json(item, include_fraud=include_fraud) for item in claim.documents],
            "total": len(claim.documents)}


@bp.get("/documents/<int:document_id>")
@role_required("customer", "employee", "reviewer", "admin")
def document_details(document_id):
    return {"document": document_json(accessible_document(document_id),
        include_fraud=current_user.role in {"employee", "reviewer", "admin"})}


@bp.get("/documents/<int:document_id>/ocr")
@role_required("customer", "employee", "reviewer", "admin")
def document_ocr(document_id):
    return {"document": document_json(accessible_document(document_id), include_text=True,
        include_fraud=current_user.role in {"employee", "reviewer", "admin"})}


@bp.get("/documents/<int:document_id>/content")
@role_required("customer", "employee", "reviewer", "admin")
def document_content(document_id):
    document = accessible_document(document_id)
    return send_file(BytesIO(storage().open(document.storage_path)), mimetype=document.mime_type,
        as_attachment=True, download_name=document.original_filename)


@bp.patch("/documents/<int:document_id>/ocr-review")
@customer_only
def review_ocr(document_id):
    data = body(OCRReviewSchema())
    document = accessible_document(document_id)
    claim = document.claim
    if document.ocr_status in {"pending", "processing"}:
        raise Conflict("Wait for document processing to finish before confirming corrections.")
    from backend.services.evidence import applicable_fields
    allowed = set(applicable_fields(document.document_type))
    if not allowed:
        from marshmallow import ValidationError
        raise ValidationError({"document": ["This evidence has no purchase fields to confirm."]})
    extras = set(data) - allowed - {"version", "confirm", "warranty_duration_unit"}
    if extras or ("warranty_duration_unit" in data and "warranty_duration" not in allowed):
        from marshmallow import ValidationError
        raise ValidationError({"document": ["Correction fields do not apply to this evidence category."]})
    if "warranty_duration" in allowed:
        previous = confirmation(document, "warranty_duration") or {}
        observed = extraction(document, "warranty_duration")
        duration = data.get("warranty_duration", previous.get("confirmed_value", observed.get("value")))
        unit = data.get("warranty_duration_unit", previous.get("unit", observed.get("unit")))
        if duration is not None and (isinstance(duration, bool) or not isinstance(duration, int) or
                unit not in {"months", "years"} or
                (unit == "years" and duration > 100)):
            from marshmallow import ValidationError
            raise ValidationError({"warranty_duration": ["Specify months or years and at most 1200 months of coverage."]})
    _lock_review_claim(claim, data.pop("version"))
    data.pop("confirm")
    corrected = review_document(document, data, current_user.id)
    audit("ocr_reviewed", document, new={"corrected": corrected}, claim_id=claim.id)
    if corrected:
        changes = {
            field: {"ocr_value": value.get("ocr_value"), "confirmed_value": value.get("confirmed_value")}
            for field, value in (document.verified_data or {}).items()
            if value.get("was_corrected")
        }
        # The extracted OCR result remains on the document; this event records
        # the exact human correction that produced the verified value.
        audit("ocr_corrected", document, old={field: item["ocr_value"] for field, item in changes.items()},
              new={field: item["confirmed_value"] for field, item in changes.items()}, claim_id=claim.id)
    identity = (document.id, document.document_id, claim.id)
    db.session.commit()
    document = processing_document(*identity)
    return {"document": document_json(document, include_text=True), "claim": workflow_json(claim)}


@bp.post("/documents/<int:document_id>/ocr/retry")
@customer_only
def retry_ocr(document_id):
    data = body(DocumentMutationSchema())
    document = accessible_document(document_id)
    claim = document.claim
    if not can_retry(document):
        raise Conflict("This evidence is not retryable yet. Wait for active processing to finish.")
    _lock_review_claim(claim, data["version"])
    content = storage().open(document.storage_path)
    identity = (document.id, document.document_id, claim.id)
    start_attempt(document)
    db.session.commit()
    document = processing_document(*identity)
    audit("ocr_started", document, new={"retry": True}, claim_id=claim.id)
    action = process_document(document, content, force=True)
    audit(action, document, new={"retry": True, "status": document.ocr_status}, claim_id=claim.id)
    db.session.commit()
    document = processing_document(*identity)
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
