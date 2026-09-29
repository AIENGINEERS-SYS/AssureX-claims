"""Prediction, combined evaluation and decision retrieval APIs."""
from time import monotonic
from flask import Blueprint, current_app
from flask_jwt_extended import current_user
from sqlalchemy import select
from sqlalchemy.orm import joinedload, selectinload
from werkzeug.exceptions import Conflict, NotFound, ServiceUnavailable
from backend.db.models import Claim, Document, Product
from backend.extensions import db, limiter
from backend.security import role_required
from backend.services.evaluation import (ClaimEvaluationService, evaluation_json, latest_evaluation,
                                          policy_for_claim)
from backend.services.predictions import GTMPredictionService, PredictionError, PythonPredictionService
from .common import audit
from .schemas import ClaimIdSchema, body

bp = Blueprint("predictions", __name__, url_prefix="/api")


def _loaded_claim(claim_id):
    query = select(Claim).where(Claim.id == claim_id, Claim.status != "draft").options(
        joinedload(Claim.product), joinedload(Claim.warranty), selectinload(Claim.documents),
        selectinload(Claim.repairs))
    if current_user.role == "employee":
        query = query.where(Claim.assigned_employee_id == current_user.id)
    elif current_user.role == "reviewer":
        query = query.where((Claim.assigned_reviewer_id.is_(None)) |
                            (Claim.assigned_reviewer_id == current_user.id))
    claim = db.session.scalar(query)
    if claim is None:
        raise NotFound("Claim not found.")
    if claim.product is None:
        raise Conflict("Claim has no registered product to evaluate.")
    return claim


def _run_single(kind):
    started = monotonic()
    claim_id = body(ClaimIdSchema())["claim_id"]
    claim = _loaded_claim(claim_id)
    policy, warning = policy_for_claim(claim)
    service = PythonPredictionService() if kind == "python" else GTMPredictionService()
    try:
        result, record = service.predict(claim, policy)
    except PredictionError as exc:
        current_app.logger.error("%s inference endpoint failed for claim_id=%s (%s)",
                                 kind, claim.id, type(exc.__cause__ or exc).__name__)
        raise ServiceUnavailable(f"{kind.upper()} prediction is temporarily unavailable.") from exc
    audit(f"prediction.{kind}", record, new={
        "prediction_class": result["prediction_class"],
        "confidence": result["confidence"],
        "model_version": result["model_version"],
        "inference_duration_ms": result.get("inference_duration_ms"),
    }, claim_id=claim.id)
    db.session.commit()
    if warning:
        result["policy_warning"] = warning
    total_ms = round((monotonic() - started) * 1000)
    target_ms = current_app.config["MODEL_PERFORMANCE_TARGET_MS"]
    result["total_duration_ms"] = total_ms
    result["performance_target_ms"] = target_ms
    result["within_performance_target"] = total_ms <= target_ms
    log = current_app.logger.info if total_ms <= target_ms else current_app.logger.warning
    log("prediction_complete model=%s claim_id=%s total_ms=%s target_ms=%s",
        kind, claim.id, total_ms, target_ms)
    return result, 201


@bp.post("/predict/python")
@role_required("employee", "reviewer")
@limiter.limit("20 per minute")
def python_prediction():
    return _run_single("python")


@bp.post("/predict/gtm")
@role_required("employee", "reviewer")
@limiter.limit("20 per minute")
def gtm_prediction():
    return _run_single("gtm")


@bp.post("/claims/<int:claim_id>/evaluate")
@role_required("employee", "reviewer")
@limiter.limit("10 per minute")
def evaluate(claim_id):
    started = monotonic()
    claim = _loaded_claim(claim_id)
    previous_status = claim.status
    item, rules, policy = ClaimEvaluationService().evaluate(claim)
    # Link the invoking user to immutable inference records created by the
    # combined evaluation, without duplicating raw OCR/document evidence.
    for kind, record in (("python", item.python_prediction), ("gtm", item.gtm_prediction)):
        if record is not None:
            audit(f"prediction.{kind}", record, new={
                "prediction_class": record.predicted_class,
                "confidence": {
                    "valid": record.confidence_valid,
                    "invalid": record.confidence_invalid,
                    "manual_review": record.confidence_manual_review,
                },
                "model_version": record.model_version.version,
                "inference_duration_ms": record.inference_duration_ms,
                "source": "claim_evaluation",
            }, claim_id=claim.id)
    audit("rule.executed", item, new={
        "policy_code": policy.code,
        "policy_version": policy.version,
        "rule_count": len(rules),
        "results": {rule["rule_code"]: rule["result"] for rule in rules},
    }, claim_id=claim.id)
    audit("claim.evaluate", item, new={"status": item.status, "recommendation": item.recommendation},
          claim_id=claim.id)
    if claim.status != previous_status:
        audit("claim.status_changed", claim, old={"status": previous_status},
              new={"status": claim.status, "source": "claim_evaluation"}, claim_id=claim.id)
    db.session.commit()
    total_ms = round((monotonic() - started) * 1000)
    target_ms = current_app.config["MODEL_PERFORMANCE_TARGET_MS"]
    performance = {"total_duration_ms": total_ms, "target_ms": target_ms,
                   "within_target": total_ms <= target_ms}
    log = current_app.logger.info if total_ms <= target_ms else current_app.logger.warning
    log("claim_evaluation_complete claim_id=%s total_ms=%s target_ms=%s",
        claim.id, total_ms, target_ms)
    return {"evaluation": evaluation_json(item), "policy": policy.as_dict(), "rules": rules,
            "performance": performance}, 201


@bp.get("/claims/<int:claim_id>/decision")
@role_required("customer", "employee", "reviewer")
def decision(claim_id):
    claim = db.session.get(Claim, claim_id)
    if claim is None or (current_user.role == "customer" and claim.user_id != current_user.id):
        raise NotFound("Claim not found.")
    if current_user.role == "employee" and claim.assigned_employee_id != current_user.id:
        raise NotFound("Claim not found.")
    if current_user.role == "reviewer" and claim.assigned_reviewer_id not in (None, current_user.id):
        raise NotFound("Claim not found.")
    item = latest_evaluation(claim.id)
    if item is None:
        raise NotFound("This claim has not been evaluated.")
    return {"evaluation": evaluation_json(item, technical=current_user.role != "customer")}
