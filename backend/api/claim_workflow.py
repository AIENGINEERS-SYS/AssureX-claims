"""Customer draft, document and final submission API."""
from uuid import uuid4
from flask import Blueprint, request
from flask_jwt_extended import current_user
from marshmallow import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from werkzeug.exceptions import BadRequest, NotFound
from backend.db.models import Claim, Product, utcnow
from backend.extensions import db
from backend.services.claim_storage import storage
from backend.services.claim_submission import (apply_draft, customer_only, document_json, lock_draft,
    next_claim_id, owned_claim, validation_errors, workflow_json)
from backend.services.products import product_json
from backend.services.warranty_calculations import current_date
from .claim_schemas import DraftSchema, DraftUpdateSchema, SubmitSchema, UploadSchema
from .common import audit, page
from .schemas import body

bp = Blueprint("claim_workflow", __name__, url_prefix="/api")


@bp.get("/products/my")
@customer_only
def products():
    return page(select(Product).where(Product.user_id == current_user.id).options(
        selectinload(Product.warranties)).order_by(Product.id.desc()), lambda p: product_json(p, current_date()))


@bp.post("/claims/draft")
@customer_only
def create():
    data = body(DraftSchema())
    claim = Claim(user_id=current_user.id, claim_id="DRF-" + uuid4().hex[:20], status="draft")
    apply_draft(claim, data)
    db.session.add(claim)
    db.session.flush()
    audit("claim.draft.create", claim, claim_id=claim.id)
    db.session.commit()
    return {"claim": workflow_json(claim)}, 201


@bp.put("/claims/draft/<int:draft_id>")
@customer_only
def update(draft_id):
    data = body(DraftUpdateSchema())
    claim = owned_claim(draft_id)
    lock_draft(claim, data.pop("version"))
    apply_draft(claim, data)
    db.session.flush()
    audit("claim.draft.update", claim, new={"current_step": claim.current_step}, claim_id=claim.id)
    db.session.commit()
    return {"claim": workflow_json(claim)}


@bp.post("/claims/upload")
@customer_only
def upload():
    data = UploadSchema().load(request.form.to_dict())
    if len(request.files.getlist("file")) != 1 or set(request.files) != {"file"}:
        from backend.services.document_errors import document_error
        raise document_error("unsupported_document", "Upload exactly one document per request.")
    claim = owned_claim(data["draft_id"])
    from .documents import upload_to_claim
    document = upload_to_claim(claim, data, request.files["file"])
    return {"document": document_json(document), "claim": workflow_json(claim)}, 201


@bp.delete("/claims/draft/<int:draft_id>/documents/<int:document_id>")
@customer_only
def remove_document(draft_id, document_id):
    data = body(SubmitSchema())
    if data["draft_id"] != draft_id:
        raise BadRequest("Draft ID does not match the URL.")
    claim = owned_claim(draft_id)
    lock_draft(claim, data["version"])
    document = next((d for d in claim.documents if d.id == document_id), None)
    if document is None:
        raise NotFound("Document not found.")
    key = document.storage_path
    audit("document_deleted", document, claim_id=claim.id)
    db.session.delete(document)
    db.session.commit()
    # After commit, a storage failure may leave an inaccessible orphan, never a broken live reference.
    try:
        storage().delete(key)
    except Exception:
        from flask import current_app
        current_app.logger.error("Orphan document cleanup required: %s", key)
    return {"claim": workflow_json(claim)}


@bp.post("/claims/submit")
@bp.post("/claims")
@customer_only
def submit():
    data = body(SubmitSchema())
    claim = owned_claim(data["draft_id"])
    # Safe retry after a lost response returns the same ID and never creates another claim.
    if claim.status != "draft":
        return {"claim": workflow_json(claim)}
    lock_draft(claim, data["version"])
    errors = validation_errors(claim, verify_files=True)
    if errors:
        raise ValidationError(errors)
    now = utcnow()
    claim.claim_id = next_claim_id(now.year)
    claim.status, claim.submitted_at, claim.submission_date = "submitted", now, now.date()
    claim.manual_review_required = any(document.ocr_status in {"failed", "review_required", "completed_with_warnings"}
        or document.cross_claim_duplicate for document in claim.documents)
    claim.current_step = 4
    audit("claim.submit", claim, new={"claim_id": claim.claim_id, "status": "SUBMITTED"}, claim_id=claim.id)
    db.session.commit()
    return {"claim": workflow_json(claim)}, 201


@bp.get("/claims/<identifier>")
@customer_only
def details(identifier):
    claim = owned_claim(identifier)
    return {"claim": workflow_json(claim),
            "validation_errors": validation_errors(claim) if claim.status == "draft" else {}}
