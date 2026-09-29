"""Model integration, decision evidence and reviewer override tests."""
from datetime import date, timedelta
from decimal import Decimal
import pytest
from sqlalchemy import select
from backend.db.models import AuditLog, Claim, EvaluationResult, Review
from backend.extensions import db
from backend.services.claim_rules import ClaimRuleEngine
from backend.services.decision_engine import DecisionEngine
from backend.services.model_comparison import ModelComparisonService
from backend.services.predictions import PredictionError, normalized_result
from backend.services.warranty_policy import WarrantyPolicyService
from test_auth import app, client, accounts, claims, bearer, login  # noqa: F401


def auth(client, role):
    return bearer(login(client, role)["access_token"])


def output(kind, predicted="valid", top=.8):
    rest = round((1 - top) / 2, 6)
    scores = {key: rest for key in ("valid", "invalid", "manual_review")}
    scores[predicted] = top
    return {"prediction": predicted, "prediction_class": predicted, "confidence": scores,
            "top_confidence": top, "model": kind, "model_version": "test", "inference_duration_ms": 1}


def test_probability_and_label_normalization():
    result = normalized_result("gtm", "v1", ["Valid Claim", "Invalid Claim", "Manual Review"],
                               [.2, .3, .5], 2)
    assert result["prediction_class"] == "manual_review"
    assert result["confidence"] == {"valid": .2, "invalid": .3, "manual_review": .5}
    with pytest.raises(PredictionError):
        normalized_result("gtm", "v1", ["Valid", "Invalid", "Unknown"], [.2, .3, .5], 2)


@pytest.mark.parametrize(("py", "gtm", "expected"), [
    (("valid", .86), ("valid", .82), "Strong Match"),
    (("valid", .74), ("valid", .67), "Acceptable Match"),
    (("valid", .64), ("valid", .62), "Weak Match"),
    (("valid", .85), ("invalid", .84), "Model Disagreement"),
    (("valid", .44), ("valid", .82), "Uncertain Result"),
])
def test_comparison_boundaries(app, py, gtm, expected):
    with app.app_context():
        assert ModelComparisonService().compare(output("python", *py), output("gtm", *gtm))["status"] == expected


def test_policy_and_structured_rules(app, claims):
    with app.app_context():
        claim = db.session.get(Claim, claims["customer"]["claim"])
        claim.submission_date = date.today()
        claim.product.purchase_date = date.today() - timedelta(days=40)
        policy = WarrantyPolicyService(app.config["WARRANTY_POLICY_PATH"]).for_product(claim.product)
        rules, contradictions = ClaimRuleEngine().evaluate(claim, policy)
        by_code = {rule["rule_code"]: rule for rule in rules}
        assert policy.code == "electronics"
        assert by_code["warranty_expiry"]["passed"] is True
        assert by_code["missing_documents"]["result"] == "manual_review"
        assert contradictions == []
        claim.fault_date = date.today() + timedelta(days=1)
        _, contradictions = ClaimRuleEngine().evaluate(claim, policy)
        assert contradictions[0]["type"] == "fault_after_claim"


def test_decision_rules_do_not_let_models_override_expiry():
    rules = [{"rule_code": "warranty_expiry", "result": "failed", "severity": "high",
              "message": "Expired", "passed": False}]
    comparison = {"status": "Strong Match"}
    decision, explanation = DecisionEngine().decide(output("python"), output("gtm"), comparison,
        rules, [], {"duplicate_risk": "low"})
    assert decision == "likely_invalid"
    assert explanation["problems"][0]["code"] == "warranty_expiry"


def test_combined_evaluation_keeps_python_when_gtm_fails(client, app, accounts, claims, monkeypatch):
    class Python:
        def predict(self, claim, policy):
            return output("python"), None

    class GTM:
        def predict(self, claim, policy):
            raise PredictionError("GTM inference failed.")

    monkeypatch.setattr("backend.services.evaluation.PythonPredictionService", Python)
    monkeypatch.setattr("backend.services.evaluation.GTMPredictionService", GTM)
    response = client.post(f"/api/claims/{claims['customer']['claim']}/evaluate", headers=auth(client, "reviewer"))
    assert response.status_code == 201, response.json
    assert response.json["evaluation"]["status"] == "partial"
    assert response.json["evaluation"]["comparison"]["python_prediction"] == "valid"
    assert response.json["evaluation"]["model_errors"] == {"gtm": "GTM inference failed."}
    with app.app_context():
        saved = db.session.query(EvaluationResult).one()
        assert saved.recommendation == "manual_review_required"


def test_real_python_and_injected_gtm_prediction_routes(client, app, claims):
    employee = auth(client, "employee")
    python_response = client.post("/api/predict/python", headers=employee,
                                  json={"claim_id": claims["customer"]["claim"]})
    assert python_response.status_code == 201, python_response.json
    assert python_response.json["prediction_class"] in {"valid", "invalid", "manual_review"}
    assert sum(python_response.json["confidence"].values()) == pytest.approx(1, abs=.001)
    app.config["GTM_PREDICTOR"] = lambda path: [.76, .14, .10]
    gtm_response = client.post("/api/predict/gtm", headers=employee,
                               json={"claim_id": claims["customer"]["claim"]})
    assert gtm_response.status_code == 201, gtm_response.json
    assert gtm_response.json["prediction_class"] == "valid"


def test_combined_evaluation_records_predictions_rules_and_status(client, app, claims, tmp_path):
    app.config["GTM_PREDICTOR"] = lambda path: [.85, .10, .05]
    app.config["MODEL_CARD_PATH"] = str(tmp_path / "cards")
    claim_id = claims["customer"]["claim"]
    response = client.post(f"/api/claims/{claim_id}/evaluate", headers=auth(client, "reviewer"))
    assert response.status_code == 201, response.json
    with app.app_context():
        events = db.session.scalars(select(AuditLog).where(AuditLog.claim_id == claim_id)).all()
        actions = {event.action for event in events}
        assert {"prediction.python", "prediction.gtm", "rule.executed", "claim.evaluate",
                "claim.status_changed"} <= actions
        rule_event = next(event for event in events if event.action == "rule.executed")
        assert rule_event.new_values["policy_code"] == "electronics"
        assert rule_event.new_values["rule_count"] > 0


def test_override_requires_explicit_reason_and_preserves_machine_recommendation(client, app, claims):
    claim_id = claims["customer"]["claim"]
    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        claim.status, claim.manual_review_required = "manual_review", True
        claim.final_decision = "likely_invalid"
        db.session.commit()
    reviewer = auth(client, "reviewer")
    conflict = client.post(f"/api/review/{claim_id}/approve", headers=reviewer, json={"notes": "Evidence is valid."})
    assert conflict.status_code == 409
    invalid = client.post(f"/api/review/{claim_id}/override", headers=reviewer,
        json={"decision": "approve", "notes": "Evidence is valid.", "override_reason": " "})
    assert invalid.status_code == 400
    response = client.post(f"/api/review/{claim_id}/override", headers=reviewer,
        json={"decision": "approve", "notes": "Evidence is valid.",
              "override_reason": "Verified the original receipt with the retailer."})
    assert response.status_code == 200, response.json
    with app.app_context():
        review = db.session.query(Review).one()
        assert review.previous_decision == "likely_invalid"
        assert review.override_applied is True
        assert review.override_reason.startswith("Verified")
