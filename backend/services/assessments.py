"""One assessment pipeline for submissions, workers and authorized staff requests.

Queue state is independent of Claim.status. Every inference attempt and report
references frozen evidence; only human review may populate final_decision.
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic
from uuid import uuid4
from flask import current_app
from sqlalchemy import func, select, update
from werkzeug.exceptions import Conflict
from backend.db.assessment_models import AssessmentAttempt, AssessmentJob, AssessmentReport
from backend.db.models import AuditLog, Claim, EvaluationResult, utcnow
from backend.extensions import db
from .access import get_claim
from .evaluation import ClaimEvaluationService
from . import assessment_reports

ACTIVE = ("queued", "running", "retry_wait")
FINAL = ("approved", "rejected", "closed")


def plain(value):
    def convert(item):
        if isinstance(item, datetime) and item.tzinfo is None:
            item = item.replace(tzinfo=timezone.utc)
        return item.isoformat() if hasattr(item, "isoformat") else str(item)
    return json.loads(json.dumps(value, default=convert))


def fields(record, names):
    return plain({key: getattr(record, key, None) for key in names.split()}) if record else None


def evidence(claim):
    """Include all evaluation inputs; workflow/assignment changes aren't new evidence."""
    config = {}
    for key in ("PYTHON_MODEL_PATH", "GTM_MODEL_PATH", "WARRANTY_POLICY_PATH"):
        path = Path(current_app.config[key])
        # Hash all model files (including GTM weights/metadata), not just filenames.
        paths = sorted(path.parent.glob("*")) if key == "GTM_MODEL_PATH" else [path]
        config[key] = {p.name: file_digest(p) for p in paths if p.is_file() and p.suffix in {".bin", ".json", ".joblib", ".pkl"}}
    config.update({key: value for key, value in current_app.config.items()
        if key.startswith(("MODEL_STRONG_", "MODEL_ACCEPTABLE_", "MODEL_MINIMUM_", "DUPLICATE_", "OCR_HIGH_", "OCR_REVIEW_"))})
    return {
        "claim": fields(claim, "id claim_id submitted_at submission_date fault_date fault_type fault_description damage_type repair_history previous_replacement"),
        "product": fields(claim.product, "id name category brand model_number serial_number purchase_date purchase_price retailer purchase_metadata"),
        "warranty": fields(claim.warranty, "id provider warranty_type start_date expiry_date coverage_duration_months coverage_conditions exclusions extended_warranty service_center_requirements"),
        "documents": [fields(d, "id document_id document_type original_filename file_hash file_size ocr_status review_status extracted_data verified_data processing_metadata ocr_confidence ocr_text cross_claim_duplicate")
                      for d in sorted(claim.documents, key=lambda item: item.id)],
        "repairs": [fields(r, "id repair_date service_center_name authorized_service_center parts_replaced repair_outcome repair_cost notes") for r in sorted(claim.repairs, key=lambda item: item.id)],
        "configuration": config,
    }


def file_digest(path):
    digest = hashlib.sha256()
    if not path.is_file():
        return None
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def digest(snapshot):
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def event(action, job, *, actor=None, details=None):
    item = AuditLog(user_id=getattr(actor, "id", None), claim_id=job.claim_id,
        action=action, entity_type="assessment_jobs", entity_id=job.id,
        new_values=plain({"assessment_version": job.version, "evidence_digest": job.evidence_digest, **(details or {})}))
    db.session.add(item)
    return item


def enqueue(claim, *, actor=None, retry=False):
    if actor is not None:
        claim = get_claim(claim.id, actor, lock=True)
    if claim.status == "draft" or claim.status in FINAL:
        raise Conflict("Only submitted, undecided claims can be assessed.")
    if claim.product is None:
        raise Conflict("Claim has no registered product to evaluate.")
    # Conditional write also serializes scheduling on SQLite, where FOR UPDATE is ignored.
    changed = db.session.execute(update(Claim).where(Claim.id == claim.id, Claim.version == claim.version)
        .values(version=Claim.version + 1), execution_options={"synchronize_session": False})
    if changed.rowcount != 1:
        raise Conflict("The claim changed. Refresh before retrying.")
    db.session.refresh(claim)
    snapshot = evidence(claim)
    fingerprint = digest(snapshot)
    # Idempotence applies to the current evidence version. If a customer returns
    # to earlier input values after a reassessment, append a new version rather
    # than reviving a historical report behind a newer superseded job.
    existing = latest(claim.id)
    if existing and existing.evidence_digest != fingerprint:
        existing = None
    active = db.session.scalar(select(AssessmentJob).where(AssessmentJob.claim_id == claim.id,
        AssessmentJob.status.in_(ACTIVE)))
    if active and active.status == "running":
        if active.evidence_digest == fingerprint:
            return active
        raise Conflict("An assessment is already processing this claim. Refresh when it finishes.")
    if active and active.evidence_digest != fingerprint:
        active.status, active.lease_token = "superseded", None
        event("assessment.superseded", active, actor=actor)
        db.session.flush()
    if existing:
        if retry and existing.status in {"failed", "superseded"}:
            existing.status, existing.available_at = "queued", utcnow()
            existing.attempts = existing.report_attempts = 0
            existing.error_code = None
            event("assessment.retry_requested", existing, actor=actor)
        return existing
    version = (db.session.scalar(select(func.max(AssessmentJob.version)).where(AssessmentJob.claim_id == claim.id)) or 0) + 1
    job = AssessmentJob(claim_id=claim.id, version=version, requested_by=getattr(actor, "id", None),
        evidence_digest=fingerprint, evidence_snapshot=snapshot)
    db.session.add(job)
    db.session.flush()
    event("assessment.queued", job, actor=actor)
    return job


def latest(claim_id):
    return db.session.scalar(select(AssessmentJob).where(AssessmentJob.claim_id == claim_id)
        .order_by(AssessmentJob.version.desc()).limit(1))


def public(job):
    from .claim_submission import timestamp
    if job is None:
        return {"status": "not_scheduled", "assessment_status": "not_scheduled", "report_status": "pending", "report": None}
    evaluation_complete = bool(job.evaluation_snapshot and job.evaluation_snapshot.get("status") == "complete")
    report = db.session.scalar(select(AssessmentReport).where(AssessmentReport.assessment_id == job.id))
    messages = {
        "policy_unavailable": "A matching warranty policy is unavailable. Your claim is saved for review.",
        "evidence_processing": "Evidence processing is not complete. Please wait or ask your service center to recover it.",
        "model_unavailable": "Assessment is temporarily unavailable. Your submitted claim is saved.",
        "evidence_unavailable": "Some saved evidence could not be verified. Please contact support.",
        "processing_failed": "Assessment processing was interrupted. Your claim is saved.",
        "report_failed": "Assessment completed, but the report could not be generated. It can be retried safely.",
        "evidence_changed": "Evidence changed during assessment. Request assessment of the updated evidence.",
        "lease_expired": "Processing was interrupted and will be retried.",
    }
    return {"id": job.id, "version": job.version, "status": job.status,
        "assessment_status": "completed" if evaluation_complete else job.status,
        "report_status": job.report_status, "attempts": job.attempts,
        "retryable": job.status in {"failed", "superseded"},
        "message": messages.get(job.error_code),
        "next_attempt_at": timestamp(job.available_at) if job.status == "retry_wait" else None,
        "report": ({"id": report.id, "version": report.version,
            "url": f"/claims/{job.claim_id}/assessment/reports/{report.id}"} if report else None)}


def take(job_id=None):
    now = utcnow()
    query = select(AssessmentJob.id).where(AssessmentJob.status.in_(("queued", "retry_wait")),
        AssessmentJob.available_at <= now)
    if job_id:
        query = query.where(AssessmentJob.id == job_id)
    identity = db.session.scalar(query.order_by(AssessmentJob.created_at).limit(1).with_for_update(skip_locked=True))
    if identity is None:
        db.session.rollback()
        return None
    token = str(uuid4())
    candidate = db.session.get(AssessmentJob, identity)
    report_stage = bool(candidate.evaluation_snapshot and candidate.evaluation_snapshot.get("status") == "complete")
    changed = db.session.execute(update(AssessmentJob).where(AssessmentJob.id == identity,
        AssessmentJob.status.in_(("queued", "retry_wait")), AssessmentJob.available_at <= now)
        .values(status="running", lease_token=token, report_status="running" if report_stage else "pending",
                lease_until=now + timedelta(seconds=current_app.config["ASSESSMENT_LEASE_SECONDS"]))
        .execution_options(synchronize_session=False))
    db.session.commit()
    return (identity, token) if changed.rowcount else None


def locked_job(identity, token):
    job = db.session.scalar(select(AssessmentJob).where(AssessmentJob.id == identity,
        AssessmentJob.status == "running", AssessmentJob.lease_token == token).with_for_update())
    if job is None:
        raise Conflict("Assessment is already being processed or its worker lease changed.")
    return job


def locked_context(identity, token):
    candidate = db.session.get(AssessmentJob, identity)
    if candidate is None:
        raise Conflict("Assessment is unavailable.")
    # The same lock order as submission/evidence/review operations prevents a
    # worker and an interactive request from waiting on each other's rows.
    claim = db.session.scalar(select(Claim).where(Claim.id == candidate.claim_id).with_for_update())
    return locked_job(identity, token), claim


def fail(job, code, *, report=False):
    count = job.report_attempts if report else job.attempts
    job.error_code = code
    job.status = "retry_wait" if count < current_app.config["ASSESSMENT_MAX_ATTEMPTS"] else "failed"
    job.available_at = utcnow() + timedelta(seconds=current_app.config["ASSESSMENT_RETRY_SECONDS"] * 2 ** max(0, count - 1))
    job.lease_token = job.lease_until = None
    if report:
        job.report_status = "failed"
    event("assessment.report_failed" if report else "assessment.failed", job, details={"code": code, "attempt": count})


def run(identity, token):
    started = monotonic()
    phase = "assessment"
    try:
        candidate = db.session.get(AssessmentJob, identity)
        needs_inference = candidate is not None and not (candidate.evaluation_snapshot and candidate.evaluation_snapshot.get("status") == "complete")
        db.session.rollback()
        if needs_inference:
            from .predictions import warm_available_models
            warm_available_models(current_app)
        job, claim = locked_context(identity, token)
        complete = bool(job.evaluation_snapshot and job.evaluation_snapshot.get("status") == "complete")
        if not complete:
            job.attempts += 1
            if claim.status in FINAL or claim.status in {"draft", "additional_information_required"}:
                job.status, job.error_code = "superseded", "evidence_changed"
                job.lease_token = job.lease_until = None
                event("assessment.superseded", job)
                db.session.commit()
                return
            if digest(evidence(claim)) != job.evidence_digest:
                job.status, job.error_code = "superseded", "evidence_changed"
                job.lease_token = job.lease_until = None
                event("assessment.superseded", job)
                db.session.commit()
                return
            if any(document.ocr_status in {"pending", "processing"} for document in claim.documents):
                fail(job, "evidence_processing")
                db.session.commit()
                return
            from .claim_storage import storage
            for document in claim.documents:
                try:
                    content = storage().read(document.storage_path)
                except Exception:
                    raise EvidenceUnavailable() from None
                if len(content) != document.file_size or hashlib.sha256(content).hexdigest() != document.file_hash:
                    raise EvidenceUnavailable()
            previous = claim.status
            item, rules, policy = ClaimEvaluationService().evaluate(claim)
            if digest(evidence(claim)) != job.evidence_digest:
                raise Conflict("Evidence changed during inference.")
            job.evaluation_id = item.id
            job.evaluation_snapshot = plain({"status": item.status, "rules": rules, "policy": policy.as_dict()})
            db.session.add(AssessmentAttempt(assessment_id=job.id, evaluation_id=item.id,
                snapshot=job.evaluation_snapshot, duration_ms=round((monotonic() - started) * 1000)))
            audits = [event("claim.evaluate", job, details={"evaluation_id": item.evaluation_id, "status": item.status}),
                      event("rule.executed", job, details={"policy_code": policy.code,
                        "policy_version": policy.version, "rule_count": len(rules)})]
            for kind, record in (("python", item.python_prediction), ("gtm", item.gtm_prediction)):
                if record:
                    audits.append(event(f"prediction.{kind}", job, details={"prediction_id": record.prediction_id}))
            if previous != claim.status:
                audits.append(event("claim.status_changed", job, details={"prior_state": previous, "status": claim.status}))
            db.session.flush()
            job.evaluation_snapshot = {**job.evaluation_snapshot, "audit_references": [entry.audit_id for entry in audits]}
            if item.status != "complete":
                fail(job, "policy_unavailable" if item.model_errors.get("policy") else "model_unavailable")
                db.session.commit()
                return
            # Persist successful inference before report generation; a report retry
            # must not repeat expensive inference or discard a valid evaluation.
            job.report_status = "running"
            job.lease_until = utcnow() + timedelta(seconds=current_app.config["ASSESSMENT_LEASE_SECONDS"])
            db.session.commit()
        phase = "report"
        job, claim = locked_context(identity, token)
        job.report_attempts += 1
        job.report_status = "running"
        item = db.session.get(EvaluationResult, job.evaluation_id)
        report = assessment_reports.generate(job, item, claim)
        from .notifications import NotificationService
        NotificationService().send_report_available(claim, report)
        job.status, job.report_status, job.error_code = "completed", "available", None
        job.completed_at = utcnow()
        job.lease_token = job.lease_until = None
        event("assessment.report_available", job, details={"report_id": report.id})
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        # Fence stale workers: they cannot change a job claimed by another process.
        job = db.session.scalar(select(AssessmentJob).where(AssessmentJob.id == identity,
            AssessmentJob.status == "running", AssessmentJob.lease_token == token).with_for_update())
        if job is not None:
            if phase == "report":
                job.report_attempts += 1
            else:
                job.attempts += 1
            code = "report_failed" if phase == "report" else "evidence_unavailable" if isinstance(exc, EvidenceUnavailable) else "processing_failed"
            fail(job, code, report=phase == "report")
            db.session.commit()
        current_app.logger.error("assessment_failed assessment_id=%s phase=%s error_type=%s", identity, phase, type(exc).__name__)
    finally:
        current_app.logger.info("assessment_timing assessment_id=%s duration_ms=%s", identity, round((monotonic() - started) * 1000))


class EvidenceUnavailable(Exception):
    pass


def recover():
    jobs = db.session.scalars(select(AssessmentJob).where(AssessmentJob.status == "running",
        AssessmentJob.lease_until < utcnow()).with_for_update(skip_locked=True)).all()
    for job in jobs:
        complete = bool(job.evaluation_snapshot and job.evaluation_snapshot.get("status") == "complete")
        if complete:
            job.report_attempts += 1
        else:
            job.attempts += 1
        fail(job, "lease_expired", report=complete)
        event("assessment.recovered", job)
    db.session.commit()


def process_next():
    recover()
    taken = take()
    if taken:
        run(*taken)
    return bool(taken)


def evaluate_now(claim, actor):
    job = enqueue(claim, actor=actor, retry=True)
    event("assessment.run_requested", job, actor=actor)
    identity = job.id
    db.session.commit()
    taken = take(identity)
    if taken:
        run(*taken)
    job = db.session.get(AssessmentJob, identity, populate_existing=True)
    if not job.evaluation_id:
        raise Conflict("Assessment is pending or temporarily unavailable. Check assessment progress.")
    return db.session.get(EvaluationResult, job.evaluation_id), job.evaluation_snapshot
