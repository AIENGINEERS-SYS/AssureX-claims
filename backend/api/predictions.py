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
    from backend.services.access import claim_scope
    query = query.where(claim_scope(current_user))
    claim = db.session.scalar(query)
    if claim is None:
        raise NotFound("Claim not found.")
    if claim.status in {"approved", "rejected", "closed"}:
        raise Conflict("Finalized claims cannot be re-evaluated.")
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
@role_required("employee", "reviewer", "admin")
@limiter.limit("20 per minute")
def python_prediction():
    return _run_single("python")


@bp.post("/predict/gtm")
@role_required("employee", "reviewer", "admin")
@limiter.limit("20 per minute")
def gtm_prediction():
    return _run_single("gtm")


@bp.post("/claims/<int:claim_id>/evaluate")
@role_required("employee", "reviewer", "admin")
@limiter.limit("10 per minute")
def evaluate(claim_id):
    started = monotonic()
    claim = _loaded_claim(claim_id)
    from backend.services.assessments import evaluate_now
    item, snapshot = evaluate_now(claim, current_user)
    total_ms = round((monotonic() - started) * 1000)
    target_ms = current_app.config["MODEL_PERFORMANCE_TARGET_MS"]
    return {"evaluation": evaluation_json(item), "policy": snapshot["policy"], "rules": snapshot["rules"],
        "performance": {"total_duration_ms": total_ms, "target_ms": target_ms,
                        "within_target": total_ms <= target_ms}}, 201


@bp.get("/claims/<int:claim_id>/decision")
@role_required("customer", "employee", "reviewer", "admin")
def decision(claim_id):
    from backend.services.access import get_claim
    claim = get_claim(claim_id, current_user)
    item = latest_evaluation(claim.id)
    if item is None:
        raise NotFound("This claim has not been evaluated.")
    return {"evaluation": evaluation_json(item, technical=current_user.role != "customer")}
