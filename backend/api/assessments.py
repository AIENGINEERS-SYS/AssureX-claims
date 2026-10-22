"""Claim-specific progress, safe recovery and immutable customer report access."""
from io import BytesIO
from flask import Blueprint, send_file
from flask_jwt_extended import current_user
from sqlalchemy import select
from werkzeug.exceptions import BadRequest, Conflict, NotFound
from backend.db.assessment_models import AssessmentReport
from backend.extensions import db, limiter
from backend.security import role_required
from backend.services import assessments, assessment_reports
from backend.services.access import get_claim
from backend.services.claim_submission import evidence_validation_errors, workflow_json, timestamp
from backend.services.report_jobs import FORMATS
from .common import audit, page
from .schemas import StrictSchema, body

bp = Blueprint("assessments", __name__, url_prefix="/api/claims")
authenticated = role_required("customer", "employee", "reviewer", "admin")


@bp.get("/<int:claim_id>/assessment")
@authenticated
def progress(claim_id):
    claim = get_claim(claim_id, current_user)
    return {"assessment": assessments.public(assessments.latest(claim.id)),
            "claim_status": claim.status, "authorized_decision": assessment_reports.human_decision(claim.id)}


@bp.post("/<int:claim_id>/assessment/retry")
@authenticated
@limiter.limit("5 per minute", key_func=lambda: str(current_user.id))
def retry(claim_id):
    body(StrictSchema())
    claim = get_claim(claim_id, current_user, lock=True)
    job = assessments.latest(claim.id)
    if current_user.role == "customer":
        if claim.status != "additional_information_required" and (not job or job.status not in {"failed", "superseded"}):
            raise Conflict("Refresh assessment progress. A retry is not currently needed.")
    if claim.status == "additional_information_required":
        errors = evidence_validation_errors(claim, verify_files=True)
        if errors:
            from marshmallow import ValidationError
            raise ValidationError(errors)
    if claim.status == "additional_information_required":
        before = claim.status
        claim.status = "under_evaluation"
        audit("claim.status_changed", claim, old={"status": before},
            new={"status": claim.status, "source": "assessment_retry"}, claim_id=claim.id)
        # Retain the request in immutable review/audit history; the live request
        # is fulfilled only after evidence validation above.
        claim.information_request = None
    job = assessments.enqueue(claim, actor=current_user, retry=True)
    db.session.commit()
    return {"assessment": assessments.public(job), "claim": workflow_json(claim)}, 202


def report_json(report):
    return {"id": report.id, "version": report.version, "assessment_id": report.assessment_id, "status": report.status,
            "created_at": timestamp(report.created_at), "evidence_digest": report.evidence_digest}


@bp.get("/<int:claim_id>/assessment/reports")
@authenticated
def history(claim_id):
    get_claim(claim_id, current_user)
    return page(select(AssessmentReport).where(AssessmentReport.claim_id == claim_id)
        .order_by(AssessmentReport.version.desc()), report_json)


def accessible_report(claim_id, report_id):
    get_claim(claim_id, current_user)
    report = db.session.scalar(select(AssessmentReport).where(AssessmentReport.id == report_id,
        AssessmentReport.claim_id == claim_id))
    if report is None:
        raise NotFound("Report not found.")
    return report


@bp.get("/<int:claim_id>/assessment/reports/<report_id>")
@authenticated
def view(claim_id, report_id):
    report = accessible_report(claim_id, report_id)
    audit("assessment.report_viewed", report, claim_id=claim_id)
    db.session.commit()
    return {"report": report_json(report) | {"payload": report.payload},
            "authorized_decision": assessment_reports.human_decision(claim_id)}


@bp.get("/<int:claim_id>/assessment/reports/<report_id>/download/<format>")
@authenticated
@limiter.limit("10 per minute", key_func=lambda: str(current_user.id))
def download(claim_id, report_id, format):
    if format not in FORMATS:
        raise BadRequest("Choose pdf, csv or excel.")
    report = accessible_report(claim_id, report_id)
    content = assessment_reports.export_bytes(report, format)
    # Recheck assignment after rendering, before releasing any bytes.
    db.session.expire_all()
    from flask_jwt_extended import verify_jwt_in_request
    verify_jwt_in_request()
    accessible_report(claim_id, report_id)
    audit("assessment.report_downloaded", report, new={"format": format}, claim_id=claim_id)
    db.session.commit()
    extension, mimetype = FORMATS[format]
    return send_file(BytesIO(content), mimetype=mimetype, as_attachment=True,
        download_name=f"assurex-claim-{claim_id}-assessment-v{report.version}.{extension}", max_age=0)


def init_cli(app):
    import click
    import time

    @app.cli.command("assessment-worker")
    @click.option("--once", is_flag=True)
    @click.option("--poll-seconds", default=5, type=click.IntRange(1, 60))
    def worker(once, poll_seconds):
        """Process durable assessment jobs with the same services as staff requests."""
        while True:
            processed = assessments.process_next()
            if once:
                break
            if not processed:
                time.sleep(poll_seconds)
