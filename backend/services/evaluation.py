"""End-to-end claim evaluation orchestration and serialization."""
from flask import current_app
from sqlalchemy import select
from backend.db.models import EvaluationResult, RuleResult
from backend.extensions import db
from .claim_rules import ClaimRuleEngine
from .decision_engine import DecisionEngine
from .duplicate_detection import DuplicateDetectionService
from .model_comparison import ModelComparisonService
from .predictions import GTMPredictionService, PredictionError, PythonPredictionService
from .warranty_policy import PolicyConfigurationError, WarrantyPolicy, WarrantyPolicyService


def policy_for_claim(claim):
    try:
        return WarrantyPolicyService(current_app.config["WARRANTY_POLICY_PATH"]).for_product(claim.product), None
    except PolicyConfigurationError as exc:
        months = claim.warranty.coverage_duration_months if claim.warranty else 12
        fallback = WarrantyPolicy("unconfigured", ((claim.product.category or "unknown").casefold(),), months,
            (), (), (), False, False, "unconfigured")
        return fallback, str(exc)


def prediction_json(record):
    if record is None:
        return None
    return {"prediction_id": record.prediction_id, "prediction_class": record.predicted_class,
            "confidence": {"valid": float(record.confidence_valid), "invalid": float(record.confidence_invalid),
                           "manual_review": float(record.confidence_manual_review)},
            "top_confidence": float(record.top_confidence),
            "model_version": record.model_version.version,
            "created_at": record.created_at.isoformat()}


def evaluation_json(item, *, technical=True):
    payload = {"evaluation_id": item.evaluation_id, "claim_id": item.claim_id, "status": item.status,
               "recommendation": item.recommendation, "explanation": item.explanation,
               "created_at": item.created_at.isoformat()}
    if technical:
        payload.update({"python_prediction": prediction_json(item.python_prediction),
                        "gtm_prediction": prediction_json(item.gtm_prediction),
                        "comparison": item.comparison, "duplicate_finding": item.duplicate_finding,
                        "contradictions": item.contradictions, "model_errors": item.model_errors,
                        "policy_code": item.policy_code, "policy_version": item.policy_version})
    return payload


class ClaimEvaluationService:
    def evaluate(self, claim):
        policy, policy_error = policy_for_claim(claim)
        errors = {"policy": policy_error} if policy_error else {}
        outputs, records = {}, {}
        for key, service in (("python", PythonPredictionService()), ("gtm", GTMPredictionService())):
            try:
                outputs[key], records[key] = service.predict(claim, policy)
            except PredictionError as exc:
                current_app.logger.error("%s inference failed for claim_id=%s (%s)",
                                         key, claim.id, type(exc.__cause__ or exc).__name__)
                errors[key] = str(exc)

        rules, contradictions = ClaimRuleEngine().evaluate(claim, policy)
        if policy_error:
            rules.insert(0, {"rule_code": "policy_configuration", "rule_name": "Warranty policy",
                "rule_category": "coverage", "result": "manual_review", "passed": False,
                "severity": "high", "message": "A matching warranty policy is unavailable.",
                "evidence": {"category": claim.product.category}})
        for rule in rules:
            db.session.add(RuleResult(claim_id=claim.id, rule_name=rule["rule_name"],
                rule_code=rule["rule_code"], rule_category=rule["rule_category"], result=rule["result"],
                severity=rule["severity"], details={"message": rule["message"], "evidence": rule["evidence"]},
                policy_version=policy.version))
        duplicate = DuplicateDetectionService().evaluate(claim)
        comparison = ModelComparisonService().compare(outputs.get("python"), outputs.get("gtm"))
        recommendation, explanation = DecisionEngine().decide(outputs.get("python"), outputs.get("gtm"),
            comparison, rules, contradictions, duplicate)
        model_failures = sum(key in errors for key in ("python", "gtm"))
        status = "complete" if model_failures == 0 and not policy_error else "failed" if model_failures == 2 else "partial"
        item = EvaluationResult(claim_id=claim.id,
            python_prediction_id=records.get("python").id if records.get("python") else None,
            gtm_prediction_id=records.get("gtm").id if records.get("gtm") else None,
            status=status, recommendation=recommendation, comparison=comparison,
            duplicate_finding=duplicate, contradictions=contradictions, explanation=explanation,
            model_errors=errors, policy_code=policy.code, policy_version=policy.version)
        db.session.add(item)
        claim.final_decision = recommendation
        if recommendation == "manual_review_required":
            claim.status, claim.manual_review_required = "manual_review", True
        elif claim.status == "submitted":
            claim.status = "under_evaluation"
        db.session.flush()
        return item, rules, policy


def latest_evaluation(claim_id):
    return db.session.scalar(select(EvaluationResult).where(EvaluationResult.claim_id == claim_id)
                             .order_by(EvaluationResult.created_at.desc(), EvaluationResult.id.desc()))
