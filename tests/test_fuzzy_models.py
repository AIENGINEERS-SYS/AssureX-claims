"""Python model, GTM, model-comparison, low-confidence and disagreement tests.

Covers the "Python model test cases", "Google Teachable Machine test cases",
"Model-comparison test cases", "Low-confidence test cases" and "Model-disagreement
test cases" sections of fuzzy.txt, plus the decision engine that consumes them.
"""
from datetime import date, timedelta
from decimal import Decimal
import json
import pytest

from backend.db.models import Claim, EvaluationResult, ModelVersion, PythonPrediction
from backend.extensions import db
from backend.services.claim_card import card_payload, render_claim_card
from backend.services.claim_rules import ClaimRuleEngine
from backend.services.decision_engine import DecisionEngine
from backend.services.duplicate_detection import DuplicateDetectionService
from backend.services.evaluation import ClaimEvaluationService, evaluation_json, latest_evaluation
from backend.services.model_comparison import ModelComparisonService
from backend.services.predictions import (CANONICAL_LABELS, PredictionError, canonical_label,
                                          normalized_result, python_features)
from fuzzy_helpers import add_document, attach_evidence, confirmed_field, policy_for, seed_claim, set_dates
from test_auth import app, client, accounts, claims, bearer, login  # noqa: F401

LOW_DUPLICATE = {"duplicate_risk": "low"}


def auth(client, role):
    return bearer(login(client, role)["access_token"])


def output(kind, predicted="valid", top=0.8):
    rest = round((1 - top) / 2, 6)
    scores = {key: rest for key in CANONICAL_LABELS}
    scores[predicted] = top
    return {"prediction": predicted, "prediction_class": predicted, "confidence": scores,
            "top_confidence": top, "model": kind, "model_version": "test", "inference_duration_ms": 1}


def passing_rules():
    return [{"rule_code": code, "rule_name": code, "rule_category": "coverage", "result": "passed",
             "passed": True, "severity": "info", "message": "ok", "evidence": {}}
            for code in ("warranty_expiry", "serial_number_verification", "contradiction_detection",
                         "missing_documents", "repair_authorization", "excluded_damage")]


# ------------------------------------------------------------- label normalization


@pytest.mark.parametrize(("raw", "expected"), [
    ("Valid Claim", "valid"), ("valid", "valid"), ("VALID", "valid"), ("approve", "valid"),
    ("Likely Valid", "valid"),
    ("Invalid Claim", "invalid"), ("invalid", "invalid"), ("REJECT", "invalid"),
    ("Likely Invalid", "invalid"),
    ("Manual Review", "manual_review"), ("manual_review", "manual_review"),
    ("Manual_Review", "manual_review"), ("needs review", "manual_review"), ("review", "manual_review"),
    ("  Valid  ", "valid"),
])
def test_canonical_label_maps_every_supported_alias(raw, expected):
    assert canonical_label(raw) == expected


@pytest.mark.parametrize("raw", ["unknown", "", "   ", None, 0, [], "maybe"])
def test_canonical_label_rejects_unsupported_labels(raw):
    with pytest.raises(PredictionError):
        canonical_label(raw)


def test_canonical_label_prefers_manual_review_over_substring_collisions():
    assert canonical_label("manual review invalid") == "manual_review"


def test_normalized_result_uses_display_labels_and_rounded_confidence():
    result = normalized_result("gtm", "v1", ["Valid Claim", "Invalid Claim", "Manual Review"],
                               [0.4, 0.35, 0.25], 7)
    assert result["prediction"] == "Likely Valid"
    assert result["prediction_class"] == "valid"
    assert result["confidence"] == {"valid": 0.4, "invalid": 0.35, "manual_review": 0.25}
    assert result["top_confidence"] == 0.4
    assert result["model"] == "gtm" and result["model_version"] == "v1"
    assert result["inference_duration_ms"] == 7


def test_normalized_result_renormalizes_probabilities_that_sum_to_one_within_tolerance():
    result = normalized_result("gtm", "v1", ["Valid", "Invalid", "Review"], [0.5, 0.3001, 0.2], 1)
    assert sum(result["confidence"].values()) == pytest.approx(1.0, abs=0.001)
    assert result["prediction_class"] == "valid"
    assert result["top_confidence"] == pytest.approx(0.50005, abs=1e-4)


def test_normalized_result_leaves_probabilities_that_already_sum_to_one_untouched():
    result = normalized_result("gtm", "v1", ["Valid", "Invalid", "Review"], [0.5, 0.3, 0.2], 1)
    assert result["confidence"] == {"valid": 0.5, "invalid": 0.3, "manual_review": 0.2}
    assert result["top_confidence"] == 0.5


@pytest.mark.parametrize("probs", [
    [0.5, 0.5],                 # too few classes
    [0.5, 0.3, 0.1, 0.1],       # too many classes
    [1.5, -0.25, -0.25],        # outside [0, 1]
    [0.9, 0.9, 0.9],            # does not sum to one
    [0.2, 0.2, 0.2],
    [0.5, 0.3, 0.21],           # 1.01 is outside the 0.01 tolerance
])
def test_normalized_result_rejects_malformed_output(probs):
    with pytest.raises(PredictionError):
        normalized_result("gtm", "v1", ["Valid", "Invalid", "Review"], probs, 1)


def test_normalized_result_breaks_ties_in_canonical_order():
    result = normalized_result("gtm", "v1", ["Valid", "Invalid", "Review"], [1 / 3] * 3, 1)
    assert result["prediction_class"] == "valid"


# ------------------------------------------------------------------- comparison


@pytest.mark.parametrize(("py", "gtm", "expected"), [
    # Strong Match: BOTH at or above 0.80 with a gap of at most 0.10
    (("valid", 0.80), ("valid", 0.80), "Strong Match"),
    (("valid", 0.90), ("valid", 0.80), "Strong Match"),
    (("valid", 0.85), ("valid", 0.75), "Acceptable Match"),   # 0.75 < 0.80 on the GTM side
    (("valid", 0.90), ("valid", 0.69), "Weak Match"),      # gap 0.21 > acceptable gap 0.20
    # Acceptable Match: both at or above 0.65 with a gap of at most 0.20
    (("valid", 0.80), ("valid", 0.69), "Acceptable Match"),
    (("valid", 0.65), ("valid", 0.85), "Acceptable Match"),
    (("valid", 0.66), ("valid", 0.66), "Acceptable Match"),
    # Weak Match: agreed class but neither band is satisfied
    (("valid", 0.64), ("valid", 0.62), "Weak Match"),
    (("valid", 0.45), ("valid", 0.90), "Weak Match"),         # exactly at the 0.45 minimum
    # Model Disagreement: classes differ and both clear the minimum
    (("valid", 0.90), ("invalid", 0.85), "Model Disagreement"),
    (("manual_review", 0.80), ("invalid", 0.46), "Model Disagreement"),
    (("valid", 0.60), ("invalid", 0.60), "Model Disagreement"),  # checked before Weak Match
    # Uncertain Result: either side is below the 0.45 minimum
    (("valid", 0.44), ("valid", 0.90), "Uncertain Result"),
    (("valid", 0.90), ("valid", 0.44), "Uncertain Result"),
    (("valid", 0.44), ("invalid", 0.44), "Uncertain Result"),
])
def test_comparison_status_boundaries(app, py, gtm, expected):
    with app.app_context():
        result = ModelComparisonService().compare(output("python", *py), output("gtm", *gtm))
    assert result["status"] == expected


def test_comparison_reports_distances_and_class_agreement(app):
    with app.app_context():
        result = ModelComparisonService().compare(output("python", "valid", 0.90),
                                                  output("gtm", "valid", 0.70))
    assert result["classes_match"] is True
    assert result["confidence_difference"] == pytest.approx(0.20, abs=1e-6)
    assert result["distribution_distance"] > 0
    assert result["python_prediction"] == result["gtm_prediction"] == "valid"
    assert result["python_top_confidence"] == 0.90
    assert result["gtm_top_confidence"] == 0.70


def test_comparison_is_uncertain_when_either_model_is_missing(app):
    with app.app_context():
        only_python = ModelComparisonService().compare(output("python"), None)
        only_gtm = ModelComparisonService().compare(None, output("gtm"))
        neither = ModelComparisonService().compare(None, None)
    for result in (only_python, only_gtm, neither):
        assert result["status"] == "Uncertain Result"
        assert result["classes_match"] is None
        assert result["confidence_difference"] is None
        assert result["distribution_distance"] is None
    assert only_python["gtm_prediction"] is None and only_python["python_prediction"] == "valid"
    assert only_gtm["python_prediction"] is None and only_gtm["gtm_prediction"] == "valid"


def test_comparison_thresholds_are_ordered_by_configuration(app):
    assert app.config["MODEL_MINIMUM_CONFIDENCE"] <= app.config["MODEL_ACCEPTABLE_CONFIDENCE"]
    assert app.config["MODEL_ACCEPTABLE_CONFIDENCE"] <= app.config["MODEL_STRONG_CONFIDENCE"]
    assert app.config["MODEL_STRONG_MAX_GAP"] <= app.config["MODEL_ACCEPTABLE_MAX_GAP"]


# ------------------------------------------------------------------- decision engine


def test_all_rules_passing_and_matching_models_approves_the_claim():
    decision, explanation = DecisionEngine().decide(output("python"), output("gtm"),
        {"status": "Strong Match"}, passing_rules(), [], dict(LOW_DUPLICATE))
    assert decision == "likely_valid"
    assert explanation["decision"] == "Likely Valid"
    assert explanation["decision_class"] == "likely_valid"
    assert explanation["problems"] == []
    assert explanation["required_action"] == "Proceed with approval workflow."
    assert len(explanation["supporting_evidence"]) == 6


@pytest.mark.parametrize("rule_code", ["warranty_expiry", "excluded_damage", "repair_authorization"])
def test_a_single_invalidating_rule_forces_rejection_even_with_matching_models(rule_code):
    rules = passing_rules()
    for rule in rules:
        if rule["rule_code"] == rule_code:
            rule.update(result="failed", passed=False, severity="high")
    decision, explanation = DecisionEngine().decide(output("python"), output("gtm"),
        {"status": "Strong Match"}, rules, [], dict(LOW_DUPLICATE))
    assert decision == "likely_invalid"
    assert explanation["required_action"].startswith("Proceed with rejection workflow")


def test_invalidating_rule_with_medium_severity_does_not_force_rejection():
    rules = passing_rules()
    for rule in rules:
        if rule["rule_code"] == "warranty_expiry":
            rule.update(result="failed", passed=False, severity="medium")
    decision, _ = DecisionEngine().decide(output("python"), output("gtm"),
        {"status": "Strong Match"}, rules, [], dict(LOW_DUPLICATE))
    assert decision == "manual_review_required"


def test_both_models_saying_invalid_rejects():
    decision, _ = DecisionEngine().decide(output("python", "invalid"), output("gtm", "invalid"),
        {"status": "Strong Match"}, passing_rules(), [], dict(LOW_DUPLICATE))
    assert decision == "likely_invalid"


def test_models_agreeing_on_manual_review_escalates_to_a_human():
    decision, _ = DecisionEngine().decide(output("python", "manual_review"),
        output("gtm", "manual_review"), {"status": "Strong Match"}, passing_rules(), [],
        dict(LOW_DUPLICATE))
    assert decision == "manual_review_required"


@pytest.mark.parametrize("status", ["Model Disagreement", "Uncertain Result", "Weak Match"])
def test_degraded_comparison_always_escalates(status):
    decision, explanation = DecisionEngine().decide(output("python"), output("gtm"),
        {"status": status}, passing_rules(), [], dict(LOW_DUPLICATE))
    assert decision == "manual_review_required"
    assert any(p["code"] == "model_consistency" for p in explanation["problems"])
    assert explanation["problems"][0]["severity"] == "medium"


def test_a_missing_model_escalates_to_a_human():
    decision, _ = DecisionEngine().decide(output("python"), None, {"status": "Uncertain Result"},
        passing_rules(), [], dict(LOW_DUPLICATE))
    assert decision == "manual_review_required"


def test_manual_review_rule_escalates_and_is_listed_as_a_problem():
    rules = passing_rules()
    for rule in rules:
        if rule["rule_code"] == "missing_documents":
            rule.update(result="manual_review", passed=False, severity="medium",
                        message="Mandatory evidence is missing.")
    decision, explanation = DecisionEngine().decide(output("python"), output("gtm"),
        {"status": "Strong Match"}, rules, [], dict(LOW_DUPLICATE))
    assert decision == "manual_review_required"
    problem = next(p for p in explanation["problems"] if p["code"] == "missing_documents")
    assert problem["severity"] == "medium"
    assert problem["message"] == "Mandatory evidence is missing."


def test_serial_mismatch_escalates_even_when_every_rule_other_than_it_passes():
    rules = passing_rules()
    for rule in rules:
        if rule["rule_code"] == "serial_number_verification":
            rule.update(result="failed", passed=False, severity="high",
                        message="Serial numbers conflict across evidence.")
    decision, explanation = DecisionEngine().decide(output("python"), output("gtm"),
        {"status": "Strong Match"}, rules, [], dict(LOW_DUPLICATE))
    assert decision == "manual_review_required"
    assert any(p["code"] == "serial_number_verification" for p in explanation["problems"])


def test_high_severity_contradiction_escalates_to_a_human():
    contradictions = [{"type": "purchase_after_claim", "severity": "high",
                       "sources": ["receipt", "claim"], "values": []}]
    decision, _ = DecisionEngine().decide(output("python"), output("gtm"),
        {"status": "Strong Match"}, passing_rules(), contradictions, dict(LOW_DUPLICATE))
    assert decision == "manual_review_required"


def test_low_severity_contradiction_alone_does_not_block_approval():
    """Only high-severity contradictions force escalation; the engine sees no rule failure."""
    contradictions = [{"type": "cosmetic", "severity": "medium", "sources": [], "values": []}]
    decision, _ = DecisionEngine().decide(output("python"), output("gtm"),
        {"status": "Strong Match"}, passing_rules(), contradictions, dict(LOW_DUPLICATE))
    assert decision == "likely_valid"


@pytest.mark.parametrize(("risk", "severity"), [("medium", "medium"), ("high", "high")])
def test_duplicate_risk_blocks_automatic_approval(risk, severity):
    decision, explanation = DecisionEngine().decide(output("python"), output("gtm"),
        {"status": "Strong Match"}, passing_rules(), [], {"duplicate_risk": risk})
    assert decision == "manual_review_required"
    duplicate_problem = next(p for p in explanation["problems"] if p["code"] == "duplicate_claim_risk")
    assert duplicate_problem["severity"] == severity


def test_explanation_partitions_rules_into_support_and_problems():
    rules = passing_rules()
    rules[0].update(result="failed", passed=False, severity="high", message="Expired")
    _, explanation = DecisionEngine().decide(output("python"), output("gtm"),
        {"status": "Strong Match"}, rules, [], dict(LOW_DUPLICATE))
    assert [p["code"] for p in explanation["problems"]] == ["warranty_expiry"]
    assert len(explanation["supporting_evidence"]) == 5
    assert set(explanation) == {"decision", "decision_class", "supporting_evidence", "problems",
                                "required_action"}


# ------------------------------------------------------------------- python model


def test_real_python_model_returns_a_canonical_distribution(client, app, claims):
    response = client.post("/api/predict/python", headers=auth(client, "employee"),
                           json={"claim_id": claims["customer"]["claim"]})
    assert response.status_code == 201, response.json
    body = response.json
    assert body["model"] == "python"
    assert body["prediction_class"] in set(CANONICAL_LABELS)
    assert body["prediction"] in {"Likely Valid", "Likely Invalid", "Manual Review"}
    assert set(body["confidence"]) == set(CANONICAL_LABELS)
    assert sum(body["confidence"].values()) == pytest.approx(1.0, abs=0.01)
    assert body["top_confidence"] == pytest.approx(max(body["confidence"].values()), abs=1e-6)
    assert body["inference_duration_ms"] >= 0
    assert body["model_version"]
    assert body["prediction_id"].startswith("PYP-")


def test_real_python_model_registers_and_reuses_a_model_version(app, claims):
    from flask import current_app
    from sqlalchemy import select
    from backend.services.predictions import PythonPredictionService
    with app.app_context():
        current_app.config["MODEL_CARD_PATH"] = str(app.instance_path)
        claim = db.session.get(Claim, claims["customer"]["claim"])
        policy = policy_for(app, claim)
        first, _ = PythonPredictionService().predict(claim, policy)
        second, _ = PythonPredictionService().predict(claim, policy)
        db.session.commit()
        versions = db.session.scalars(select(ModelVersion)).all()
    assert first["model_version"] == second["model_version"]
    python_versions = [v for v in versions if v.model_type == "python"]
    assert len(python_versions) == 1
    assert python_versions[0].is_active is True
    assert python_versions[0].version == first["model_version"]


def test_python_model_persists_a_prediction_row_per_call(app, claims):
    from flask import current_app
    from sqlalchemy import select
    from backend.services.predictions import PythonPredictionService
    claim_id = claims["customer"]["claim"]
    with app.app_context():
        current_app.config["MODEL_CARD_PATH"] = str(app.instance_path)
        claim = db.session.get(Claim, claim_id)
        policy = policy_for(app, claim)
        result, record = PythonPredictionService().predict(claim, policy)
        db.session.commit()
        assert record.predicted_class == result["prediction_class"]
        assert float(record.top_confidence) == pytest.approx(result["top_confidence"], abs=1e-4)
        assert isinstance(record.confidence_valid, Decimal)
        stored = db.session.scalars(select(PythonPrediction).where(
            PythonPrediction.prediction_id == result["prediction_id"])).one()
        assert stored.claim_id == claim_id
        assert 0 <= float(stored.confidence_manual_review) <= 1
        assert stored.inference_duration_ms >= 0


def test_python_model_reads_fault_and_purchase_signals_from_the_claim(app, claims):
    from pathlib import Path
    from backend.services.predictions import _load_python
    today = date.today()
    set_dates(app, claims["customer"]["claim"], purchase_date=today - timedelta(days=10),
              fault_date=today - timedelta(days=3), claim_date_value=today)
    set_fault_text = "Screen will not power on"
    with app.app_context():
        claim = db.session.get(Claim, claims["customer"]["claim"])
        claim.fault_type, claim.fault_description = "Electrical Failure", set_fault_text
        db.session.commit()
        policy = policy_for(app, claim)
        path = Path(app.config["PYTHON_MODEL_PATH"])
        bundle = _load_python(str(path.resolve()), path.stat().st_mtime_ns)
        features = python_features(claim, policy, bundle["feature_columns"])
    assert features["fault_category"] == "electrical_failure"
    assert features["damage_cause"] == "uncertain"
    assert features["days_from_purchase_to_claim"] == 10
    assert features["fault_to_claim_days"] == 3
    assert features["purchase_to_fault_days"] == 7
    assert features["claim_month"] == today.month
    assert features["prior_repairs"] == 0
    assert features["prior_repair_authorized"] is False


def test_python_features_yield_nan_when_dates_are_missing(app, claims):
    from pathlib import Path
    from backend.services.predictions import _load_python
    set_dates(app, claims["customer"]["claim"], clear_claim_date=True, fault_date=None)
    with app.app_context():
        claim = db.session.get(Claim, claims["customer"]["claim"])
        policy = policy_for(app, claim)
        path = Path(app.config["PYTHON_MODEL_PATH"])
        bundle = _load_python(str(path.resolve()), path.stat().st_mtime_ns)
        features = python_features(claim, policy, bundle["feature_columns"])
    assert features["days_from_purchase_to_claim"] != features["days_from_purchase_to_claim"]
    assert features["claim_month"] != features["claim_month"]


def test_python_features_derive_from_the_claim_and_policy(app, claims):
    from backend.services.predictions import _load_python
    from pathlib import Path
    with app.app_context():
        claim = db.session.get(Claim, claims["customer"]["claim"])
        policy = policy_for(app, claim)
        path = Path(app.config["PYTHON_MODEL_PATH"])
        bundle = _load_python(str(path.resolve()), path.stat().st_mtime_ns)
        features = python_features(claim, policy, bundle["feature_columns"])
    assert list(features) == list(bundle["feature_columns"])
    assert features["product_category"] == "Electronics"
    assert features["policy_code"] == "electronics"
    assert features["warranty_months"] == 24
    assert features["receipt_available"] is False
    assert features["product_photo_available"] is False
    assert features["serial_or_imei_available"] is False
    assert features["document_completeness_score"] == 0.0
    assert features["claim_amount_ngn"] == 100.0


def test_python_features_switch_on_uploaded_evidence(app, accounts):
    ids = seed_claim(app, user_id=accounts["customer"])
    from pathlib import Path
    from backend.services.predictions import _load_python
    attach_evidence(app, ids["claim"], accounts["customer"])
    with app.app_context():
        claim = db.session.get(Claim, ids["claim"])
        policy = policy_for(app, claim)
        path = Path(app.config["PYTHON_MODEL_PATH"])
        bundle = _load_python(str(path.resolve()), path.stat().st_mtime_ns)
        features = python_features(claim, policy, bundle["feature_columns"])
    assert features["receipt_available"] is True
    assert features["product_photo_available"] is True
    assert features["serial_or_imei_available"] is True
    assert features["fault_evidence_type"] == "Customer photo"    # stored as fault_evidence
    assert features["document_completeness_score"] == pytest.approx(3 / 4)


def test_python_features_count_prior_repairs(app, claims):
    from fuzzy_helpers import add_repair
    add_repair(app, claims["customer"]["claim"], authorized=True)
    from pathlib import Path
    from backend.services.predictions import _load_python
    with app.app_context():
        claim = db.session.get(Claim, claims["customer"]["claim"])
        policy = policy_for(app, claim)
        path = Path(app.config["PYTHON_MODEL_PATH"])
        bundle = _load_python(str(path.resolve()), path.stat().st_mtime_ns)
        features = python_features(claim, policy, bundle["feature_columns"])
    assert features["prior_repairs"] == 1
    assert features["prior_repair_authorized"] is True


def test_missing_python_artifact_raises_a_prediction_error(app, claims, tmp_path):
    from flask import current_app
    from backend.services.predictions import PythonPredictionService
    app.config["PYTHON_MODEL_PATH"] = str(tmp_path / "absent.joblib")
    with app.app_context():
        current_app.config["MODEL_CARD_PATH"] = str(tmp_path / "cards")
        claim = db.session.get(Claim, claims["customer"]["claim"])
        policy = policy_for(app, claim)
        with pytest.raises(PredictionError):
            PythonPredictionService().predict(claim, policy)


def test_python_bundle_with_a_broken_contract_is_rejected(app, claims, tmp_path):
    import joblib
    from flask import current_app
    from backend.services.predictions import PythonPredictionService
    artifact = tmp_path / "broken.joblib"
    joblib.dump({"pipeline": object()}, artifact)
    app.config["PYTHON_MODEL_PATH"] = str(artifact)
    with app.app_context():
        current_app.config["MODEL_CARD_PATH"] = str(tmp_path / "cards")
        claim = db.session.get(Claim, claims["customer"]["claim"])
        policy = policy_for(app, claim)
        with pytest.raises(PredictionError):
            PythonPredictionService().predict(claim, policy)


# ------------------------------------------------------------------------- GTM


def test_gtm_uses_the_injected_predictor_and_records_the_claim_card(client, app, claims, tmp_path):
    from sqlalchemy import select
    from backend.db.models import GTMPrediction
    app.config["GTM_PREDICTOR"] = lambda path: [0.15, 0.15, 0.70]
    app.config["MODEL_CARD_PATH"] = str(tmp_path / "cards")
    response = client.post("/api/predict/gtm", headers=auth(client, "employee"),
                           json={"claim_id": claims["customer"]["claim"]})
    assert response.status_code == 201, response.json
    body = response.json
    assert body["model"] == "gtm"
    assert body["prediction_class"] == "manual_review"
    assert body["prediction_id"].startswith("GTP-")
    with app.app_context():
        record = db.session.scalars(select(GTMPrediction).where(
            GTMPrediction.prediction_id == body["prediction_id"])).one()
        assert record.claim_id == claims["customer"]["claim"]
        assert record.claim_summary_card_path
        assert record.claim_summary_card_path.startswith(str(tmp_path / "cards"))


def test_gtm_predictor_receives_a_rendered_claim_card(client, app, claims, tmp_path):
    seen = {}
    app.config["MODEL_CARD_PATH"] = str(tmp_path / "cards")

    def predictor(path):
        from PIL import Image
        seen["path"] = path
        seen["size"] = Image.open(path).size
        seen["mode"] = Image.open(path).mode
        return [0.80, 0.10, 0.10]

    app.config["GTM_PREDICTOR"] = predictor
    response = client.post("/api/predict/gtm", headers=auth(client, "employee"),
                           json={"claim_id": claims["customer"]["claim"]})
    assert response.status_code == 201, response.json
    assert seen["size"] == (224, 224)
    assert seen["mode"] == "RGB"


def test_gtm_claim_card_never_contains_claimant_pii(app, claims, tmp_path):
    from PIL import Image
    with app.app_context():
        claim = db.session.get(Claim, claims["customer"]["claim"])
        policy = policy_for(app, claim)
        payload = card_payload(claim, policy)
        path = render_claim_card(payload, tmp_path / "card.png")
        image = Image.open(path)
    assert set(payload) == {"claim_id", "product", "category", "brand", "model", "purchase_date",
                            "claim_date", "fault_type", "damage_type", "description", "documents",
                            "policy"}
    assert "customer@example.com" not in json.dumps(payload)
    assert image.size == (224, 224)


def test_gtm_renders_every_documented_card_variant_deterministically(app, claims, tmp_path):
    with app.app_context():
        claim = db.session.get(Claim, claims["customer"]["claim"])
        policy = policy_for(app, claim)
        payload = card_payload(claim, policy)
        first = render_claim_card(payload, tmp_path / "a.png", variant=0).read_bytes()
        again = render_claim_card(payload, tmp_path / "b.png", variant=0).read_bytes()
        other = render_claim_card(payload, tmp_path / "c.png", variant=1).read_bytes()
    assert first == again
    assert first != other


def test_gtm_returns_service_unavailable_when_the_runtime_is_missing(client, app, claims):
    app.config.pop("GTM_PREDICTOR", None)
    response = client.post("/api/predict/gtm", headers=auth(client, "employee"),
                           json={"claim_id": claims["customer"]["claim"]})
    assert response.status_code == 503
    assert response.json["error"]["code"] == "service_unavailable"


def test_gtm_rejects_a_malformed_predictor_payload(client, app, claims, tmp_path):
    app.config["MODEL_CARD_PATH"] = str(tmp_path / "cards")
    app.config["GTM_PREDICTOR"] = lambda path: [0.9, 0.9, 0.9]
    response = client.post("/api/predict/gtm", headers=auth(client, "employee"),
                           json={"claim_id": claims["customer"]["claim"]})
    assert response.status_code == 503
    assert "temporarily unavailable" in response.json["error"]["message"]


def test_gtm_predictor_may_return_a_label_to_score_mapping(client, app, claims, tmp_path):
    app.config["MODEL_CARD_PATH"] = str(tmp_path / "cards")
    app.config["GTM_PREDICTOR"] = lambda path: {"Valid": 0.1, "Invalid": 0.2, "Review": 0.7}
    response = client.post("/api/predict/gtm", headers=auth(client, "employee"),
                           json={"claim_id": claims["customer"]["claim"]})
    assert response.status_code == 201, response.json
    assert response.json["prediction_class"] == "manual_review"
    assert response.json["top_confidence"] == pytest.approx(0.7, abs=1e-6)


def test_gtm_metadata_must_declare_three_labels(app, tmp_path):
    from backend.services.predictions import _gtm_labels
    model = tmp_path / "model.json"
    model.write_text("{}", encoding="utf-8")
    metadata = tmp_path / "metadata.json"
    for payload, ok in (({"labels": ["a", "b", "c"]}, True), ({"labels": ["a", "b"]}, False),
                        ({}, False), ({"labels": "abc"}, False)):
        metadata.write_text(json.dumps(payload), encoding="utf-8")
        if ok:
            assert _gtm_labels(model) == ["a", "b", "c"]
        else:
            with pytest.raises(PredictionError):
                _gtm_labels(model)
    metadata.write_text("{not json", encoding="utf-8")
    with pytest.raises(PredictionError):
        _gtm_labels(model)


def test_gtm_artifact_version_changes_when_weights_change(tmp_path):
    from backend.services.predictions import _artifact_version
    model = tmp_path / "model.json"
    model.write_text("{}", encoding="utf-8")
    (tmp_path / "weights.bin").write_bytes(b"a")
    (tmp_path / "metadata.json").write_text("{}", encoding="utf-8")
    first = _artifact_version(model)
    assert first == _artifact_version(model)
    (tmp_path / "weights.bin").write_bytes(b"bb")
    assert _artifact_version(model) != first


def test_gtm_artifact_version_requires_all_three_files(tmp_path):
    from backend.services.predictions import _artifact_version
    model = tmp_path / "model.json"
    model.write_text("{}", encoding="utf-8")
    with pytest.raises(PredictionError):
        _artifact_version(model)
    with pytest.raises(PredictionError):
        _artifact_version(tmp_path / "absent.json")


# --------------------------------------------------------- combined evaluation


def test_evaluation_persists_predictions_rules_and_comparison(app, client, claims):
    reviewer = auth(client, "reviewer")
    app.config["GTM_PREDICTOR"] = lambda path: [0.80, 0.10, 0.10]
    response = client.post(f"/api/claims/{claims['customer']['claim']}/evaluate", headers=reviewer)
    assert response.status_code == 201, response.json
    body = response.json
    assert body["evaluation"]["status"] == "complete"
    assert body["evaluation"]["python_prediction"]["prediction_class"] in set(CANONICAL_LABELS)
    assert body["evaluation"]["gtm_prediction"]["prediction_class"] == "valid"
    assert body["evaluation"]["comparison"]["status"] in {
        "Strong Match", "Acceptable Match", "Weak Match", "Model Disagreement", "Uncertain Result"}
    assert body["policy"]["code"] == "electronics"
    assert len(body["rules"]) == 6
    with app.app_context():
        item = latest_evaluation(claims["customer"]["claim"])
        assert item is not None and item.python_prediction_id and item.gtm_prediction_id
        assert len(item.contradictions) == len(body["evaluation"]["contradictions"])
        assert item.duplicate_finding["duplicate_risk"] in {"low", "medium", "high"}


def test_evaluation_is_partial_when_only_one_model_runs(app, client, claims, monkeypatch):
    from backend.services.predictions import PredictionError as Error

    class Broken:
        def predict(self, claim, policy):
            raise Error("GTM inference failed.")

    monkeypatch.setattr("backend.services.evaluation.GTMPredictionService", Broken)
    response = client.post(f"/api/claims/{claims['customer']['claim']}/evaluate",
                           headers=auth(client, "reviewer"))
    assert response.status_code == 201, response.json
    assert response.json["evaluation"]["status"] == "partial"
    assert response.json["evaluation"]["model_errors"] == {"gtm": "GTM inference failed."}
    assert response.json["evaluation"]["gtm_prediction"] is None
    assert response.json["evaluation"]["recommendation"] == "manual_review_required"


def test_evaluation_is_failed_when_both_models_fail(app, client, claims, monkeypatch):
    from backend.services.predictions import PredictionError as Error

    class Broken:
        def predict(self, claim, policy):
            raise Error("inference failed.")

    monkeypatch.setattr("backend.services.evaluation.PythonPredictionService", Broken)
    monkeypatch.setattr("backend.services.evaluation.GTMPredictionService", Broken)
    response = client.post(f"/api/claims/{claims['customer']['claim']}/evaluate",
                           headers=auth(client, "reviewer"))
    assert response.status_code == 201, response.json
    assert response.json["evaluation"]["status"] == "failed"
    assert set(response.json["evaluation"]["model_errors"]) == {"python", "gtm"}
    assert response.json["evaluation"]["recommendation"] == "manual_review_required"


def test_evaluation_records_a_policy_configuration_problem(app, client, claims, tmp_path):
    app.config["GTM_PREDICTOR"] = lambda path: [0.80, 0.10, 0.10]
    app.config["MODEL_CARD_PATH"] = str(tmp_path / "cards")
    with app.app_context():
        db.session.get(Claim, claims["customer"]["claim"]).product.category = "Solar Inverter"
        db.session.commit()
    response = client.post(f"/api/claims/{claims['customer']['claim']}/evaluate",
                           headers=auth(client, "reviewer"))
    assert response.status_code == 201, response.json
    body = response.json
    assert body["policy"]["code"] == "unconfigured"
    assert body["rules"][0]["rule_code"] == "policy_configuration"
    assert body["rules"][0]["severity"] == "high"
    assert "policy" in body["evaluation"]["model_errors"]
    assert body["evaluation"]["recommendation"] == "manual_review_required"


def test_unconfigured_category_breaks_python_feature_derivation(app, client, claims):
    """Pins current behaviour.

    policy_for_claim falls back to a synthetic policy with no required_documents, and
    python_features divides by len(required_documents), so real Python inference raises
    and the evaluation is recorded as failed rather than partial.
    """
    with app.app_context():
        db.session.get(Claim, claims["customer"]["claim"]).product.category = "Solar Inverter"
        db.session.commit()
    response = client.post(f"/api/claims/{claims['customer']['claim']}/evaluate",
                           headers=auth(client, "reviewer"))
    assert response.status_code == 201, response.json
    body = response.json["evaluation"]
    assert body["status"] == "failed"
    assert body["python_prediction"] is None
    assert {"python", "gtm"} <= set(body["model_errors"])
    assert body["model_errors"]["python"] == "Python inference failed."
    assert "Solar Inverter" in body["model_errors"]["policy"]


def test_evaluation_of_a_claim_without_a_product_is_rejected(client, app, claims, accounts):
    with app.app_context():
        orphan = Claim(user_id=accounts["customer"], status="submitted", fault_description="x")
        db.session.add(orphan)
        db.session.commit()
        orphan_id = orphan.id
    response = client.post(f"/api/claims/{orphan_id}/evaluate", headers=auth(client, "reviewer"))
    assert response.status_code == 409
    assert response.json["error"]["code"] == "conflict"


def test_repeat_evaluations_keep_every_history_row(app, client, claims):
    reviewer = auth(client, "reviewer")
    for _ in range(2):
        assert client.post(f"/api/claims/{claims['customer']['claim']}/evaluate",
                           headers=reviewer).status_code == 201
    with app.app_context():
        assert db.session.query(EvaluationResult).filter_by(
            claim_id=claims["customer"]["claim"]).count() == 2
        assert latest_evaluation(claims["customer"]["claim"]) is not None


def test_technical_fields_are_hidden_from_customers(client, app, claims):
    reviewer = auth(client, "reviewer")
    assert client.post(f"/api/claims/{claims['customer']['claim']}/evaluate",
                       headers=reviewer).status_code == 201
    customer_view = client.get(f"/api/claims/{claims['customer']['claim']}/decision",
                               headers=auth(client, "customer"))
    assert customer_view.status_code == 200
    for hidden in ("python_prediction", "gtm_prediction", "comparison", "duplicate_finding",
                   "contradictions", "model_errors", "policy_code", "policy_version"):
        assert hidden not in customer_view.json["evaluation"]
    assert customer_view.json["evaluation"]["recommendation"]
    staff_view = client.get(f"/api/claims/{claims['customer']['claim']}/decision",
                            headers=auth(client, "reviewer"))
    assert "comparison" in staff_view.json["evaluation"]


def test_evaluation_serializes_explanation_and_created_at(app, claims):
    with app.app_context():
        claim = db.session.get(Claim, claims["customer"]["claim"])
        item, _, _ = ClaimEvaluationService().evaluate(claim)
        db.session.commit()
        body = evaluation_json(item)
    assert body["recommendation"] in {"likely_valid", "likely_invalid", "manual_review_required"}
    assert body["explanation"]["decision_class"] == body["recommendation"]
    assert body["status"] in {"complete", "partial", "failed"}
    assert body["created_at"]
    assert body["evaluation_id"].startswith("EVL-")


def test_evaluation_of_a_clean_claim_without_documents_is_still_escalated(app, client, claims):
    """Documents are the reason a configured claim escalates; the rules say why."""
    app.config["GTM_PREDICTOR"] = lambda path: [0.80, 0.10, 0.10]
    response = client.post(f"/api/claims/{claims['customer']['claim']}/evaluate",
                           headers=auth(client, "reviewer"))
    assert response.status_code == 201, response.json
    problems = {p["code"] for p in response.json["evaluation"]["explanation"]["problems"]}
    assert "missing_documents" in problems
    assert response.json["evaluation"]["recommendation"] == "manual_review_required"
