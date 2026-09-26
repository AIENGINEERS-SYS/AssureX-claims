"""Transactional workflow shared by draft, upload, review and submit endpoints."""
import hashlib
from datetime import timezone
from functools import wraps
from flask_jwt_extended import current_user
from marshmallow import ValidationError
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload
from werkzeug.exceptions import Conflict, Forbidden, NotFound
from backend.api.claim_schemas import DraftSchema, REQUIRED_DOCUMENTS
from backend.db.models import Claim, ClaimSequence, Product, utcnow
from backend.extensions import db
from backend.security import role_required
from .claim_storage import FILE_LIMIT, TOTAL_LIMIT, storage
from .products import product_json
from .warranty_calculations import current_date, select_current_warranty


def customer_only(fn):
    @role_required("customer")
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if current_user.role != "customer":
            raise Forbidden("Only customers may submit claims.")
        return fn(*args, **kwargs)
    return wrapped


def owned_claim(identifier):
    numeric = isinstance(identifier, int) or identifier.isdecimal()
    field = Claim.id if numeric else Claim.claim_id
    value = int(identifier) if numeric else identifier
    claim = db.session.scalar(select(Claim).where(field == value, Claim.user_id == current_user.id))
    if claim is None:
        raise NotFound("Claim not found.")
    return claim


def lock_draft(claim, version):
    # Conditional UPDATE also serializes writers on SQLite, where FOR UPDATE is ignored.
    result = db.session.execute(update(Claim).where(Claim.id == claim.id, Claim.status == "draft",
        Claim.version == version).values(version=Claim.version + 1, updated_at=utcnow()),
        execution_options={"synchronize_session": False})
    if result.rowcount != 1:
        raise Conflict("This draft changed or was submitted. Reload it before editing.")
    db.session.refresh(claim)


def selected_product(product_id):
    product = db.session.scalar(select(Product).where(Product.id == product_id,
        Product.user_id == current_user.id).options(selectinload(Product.warranties)).with_for_update())
    if product is None:
        raise ValidationError({"product_id": ["Select one of your registered products."]})
    if not product.is_active:
        raise ValidationError({"product_id": ["This product is inactive."]})
    return product


def apply_draft(claim, data):
    if "product_id" in data:
        if claim.product_id != data["product_id"] and claim.documents:
            raise Conflict("Remove uploaded documents before changing the product.")
        product = selected_product(data["product_id"]) if data["product_id"] else None
        claim.product = product
        claim.warranty = select_current_warranty(product.warranties) if product else None
    for key, value in data.items():
        if key != "product_id":
            setattr(claim, key, value)


def validation_errors(claim, *, verify_files=False):
    values = {key: getattr(claim, key) for key in ("product_id", "fault_type", "description",
        "damage_category", "repair_history", "previous_replacement")}
    values["fault_date"] = claim.fault_date.isoformat() if claim.fault_date else None
    errors = DraftSchema().validate(values)
    for key in ("product_id", "fault_date", "fault_type", "description", "damage_category"):
        if not values[key]:
            errors[key] = ["This field is required."]
    if claim.description and len(claim.description.strip()) < 20:
        errors["description"] = ["Use at least 20 characters."]
    if claim.product_id:
        try:
            selected_product(claim.product_id)
        except ValidationError as exc:
            errors.update(exc.messages)
    types = {"damage_evidence" if d.document_type == "fault_evidence" else d.document_type for d in claim.documents}
    missing = set(REQUIRED_DOCUMENTS) - types
    if missing:
        errors["documents"] = ["Upload: " + ", ".join(sorted(missing)) + "."]
    if sum(d.file_size for d in claim.documents) > TOTAL_LIMIT:
        errors["documents"] = ["Total upload size cannot exceed 50 MB."]
    for document in claim.documents:
        if document.file_size > FILE_LIMIT:
            errors.setdefault("documents", []).append("A file exceeds 10 MB.")
        if verify_files:
            try:
                content = storage().read(document.storage_path)
                if len(content) != document.file_size or hashlib.sha256(content).hexdigest() != document.file_hash:
                    raise NotFound()
            except NotFound:
                errors.setdefault("documents", []).append(f"Re-upload {document.original_filename}; stored file is missing or changed.")
    return errors


def next_claim_id(year):
    dialect = db.session.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise RuntimeError("Claim numbering requires PostgreSQL or SQLite")
    statement = insert(ClaimSequence).values(year=year, value=1)
    statement = statement.on_conflict_do_update(index_elements=[ClaimSequence.year],
        set_={"value": ClaimSequence.value + 1}).returning(ClaimSequence.value)
    return f"CLM-{year}-{db.session.scalar(statement):06d}"


def document_json(document):
    return {"id": document.id, "document_type": "damage_evidence" if document.document_type == "fault_evidence" else document.document_type,
        "file_name": document.original_filename, "file_type": document.mime_type, "file_size": document.file_size,
        "uploaded_at": timestamp(document.created_at), "uploaded_by": document.uploaded_by}


def timestamp(value):
    # SQLite drops timezone information; all stored application timestamps are UTC.
    return value.replace(tzinfo=timezone.utc).isoformat() if value else None


def workflow_json(claim):
    return {"id": claim.id, "claim_id": None if claim.status == "draft" else claim.claim_id,
        "customer_id": claim.user_id, "product_id": claim.product_id, "status": claim.status.upper(),
        "current_step": claim.current_step, "version": claim.version,
        "fault_date": claim.fault_date.isoformat() if claim.fault_date else None,
        "fault_type": claim.fault_type, "description": claim.description, "damage_category": claim.damage_category,
        "repair_history": claim.repair_history, "previous_replacement": claim.previous_replacement,
        "submitted_at": timestamp(claim.submitted_at),
        "submission_date": claim.submission_date.isoformat() if claim.submission_date else None,
        "created_at": timestamp(claim.created_at), "updated_at": timestamp(claim.updated_at),
        "server_date": current_date().isoformat(),
        "product": product_json(claim.product, current_date()) if claim.product else None,
        "documents": [document_json(d) for d in claim.documents]}
