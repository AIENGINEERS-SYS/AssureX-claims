"""Example claim workflows with ownership, assignment and state checks."""
import hashlib
from datetime import date
from pathlib import Path
from uuid import uuid4
from flask import Blueprint, current_app, request, send_file
from flask_jwt_extended import current_user
from sqlalchemy import select
from werkzeug.exceptions import BadRequest, Conflict, NotFound
from werkzeug.utils import secure_filename
from backend.db.models import Claim, Document, User
from backend.db.services import create_claim
from backend.extensions import db
from backend.security import role_required
from .common import audit, page
from .schemas import AssignmentSchema, ClaimSchema, StatusSchema, body, claim_json

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


@bp.post("")
@role_required("customer")
def submit():
    data = body(ClaimSchema())
    if data["fault_date"] > date.today():
        raise BadRequest("Fault date cannot be in the future.")
    try:
        claim = create_claim(db.session, user_id=current_user.id, **data)
    except ValueError:
        raise BadRequest("Product and warranty must belong to you and match each other.") from None
    claim.status, claim.submission_date = "submitted", date.today()
    audit("claim.create", claim, new={"status": claim.status}, claim_id=claim.id)
    db.session.commit()
    return {"claim": claim_json(claim)}, 201


@bp.get("/my")
@role_required("customer")
def mine():
    return page(select(Claim).where(Claim.user_id == current_user.id).order_by(Claim.id.desc()), claim_json)


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
    claim = assigned_claim(claim_id, lock=True)
    if claim.status in {"approved", "rejected", "closed"}:
        raise Conflict("Documents cannot be added to a finalized claim.")
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        raise BadRequest("Supply a file in multipart/form-data.")
    content = uploaded.read(current_app.config["MAX_CONTENT_LENGTH"] + 1)
    kind = next(((extension, mime) for signature, extension, mime in (
        (b"%PDF-", ".pdf", "application/pdf"),
        (b"\x89PNG\r\n\x1a\n", ".png", "image/png"),
        (b"\xff\xd8\xff", ".jpg", "image/jpeg")) if content.startswith(signature)), None)
    if kind is None:
        raise BadRequest("Only PDF, PNG and JPEG files are accepted.")
    root = Path(current_app.config["UPLOAD_FOLDER"]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    filename = uuid4().hex + kind[0]
    path = root / filename
    document = Document(claim_id=claim.id, uploaded_by=current_user.id, document_type="other",
        original_filename=secure_filename(uploaded.filename)[:255] or filename,
        stored_filename=filename, mime_type=kind[1], file_size=len(content),
        storage_path=filename, file_hash=hashlib.sha256(content).hexdigest())
    try:
        with path.open("xb") as stream:
            stream.write(content)
        db.session.add(document)
        db.session.flush()
        audit("document.upload", document, claim_id=claim.id)
        db.session.commit()
    except Exception:
        db.session.rollback()
        path.unlink(missing_ok=True)
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
    root = Path(current_app.config["UPLOAD_FOLDER"]).resolve()
    path = (root / document.storage_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise NotFound("Document not found.")
    return send_file(path, mimetype="application/octet-stream", as_attachment=True,
                     download_name=document.original_filename)
