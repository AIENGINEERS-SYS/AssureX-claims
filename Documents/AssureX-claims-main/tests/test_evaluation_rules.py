"""Exact boundaries and failure contracts for evaluation, independent of HTTP/DB."""
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
import json
import pytest
from backend.services.predictions import probabilities, compare_predictions, ComparisonThresholds
from backend.services.decision_engine import check_rules, decide, calendar_date
from backend.services.warranty_policy import Policy, PolicyError, load_policy


def policy_dict(code="TEST-12", months=12):
    return {"policy_code": code, "version": "test-only-v1", "months": months,
            "required_documents": ["receipt", "serial_number_image"], "exclusions": ["liquid_damage"],
            "authorized_repair_required": True, "serial_case_sensitive": False, "final_day_inclusive": True}


def valid_evidence():
    return {"purchase_date": "2024-02-29", "claim_date": "2025-02-28", "fault_date": "2025-02-20",
        "fault_type": "power", "damage_type": "manufacturing_defect", "repairs": [],
        "serials": [{"source": "claim", "value": " ABC-123 "}, {"source": "receipt", "value": "abc-123"}],
        "documents": [{"source": kind, "document_type": kind, "ocr_status": "completed",
            "review_status": "confirmed", "integrity_verified": True} for kind in ("receipt", "serial_number_image")]}


def pred(confidence="0.90", label="valid"):
    value = Decimal(confidence)
    scores = {c: (Decimal(1) - value) / 2 for c in ("valid", "invalid", "manual_review")}
    scores[label] = value
    return probabilities(scores, label)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), Decimal("NaN"), -0.1, 1.1, True, None, "bad"])
def test_nonfinite_or_invalid_probability_rejected(bad):
    with pytest.raises(ValueError):
        probabilities({"valid": bad, "invalid": .1, "manual_review": .1})


def test_distribution_and_top_class_validation():
    for scores in ({}, {"valid": 1}, {"valid": .7, "invalid": .7, "manual_review": .1}):
        with pytest.raises(ValueError):
            probabilities(scores)
    with pytest.raises(ValueError):
        probabilities({"valid": .8, "invalid": .1, "manual_review": .1}, "invalid")
    output = pred()
    output["top_confidence"] = .1
    assert compare_predictions(output, pred())["status"] == "Uncertain Result"


@pytest.mark.parametrize("left,right,expected", [
    ("0.90", "0.80", "Strong Match"), ("0.90", "0.799999", "Acceptable Match"),
    ("0.90", "0.700001", "Acceptable Match"), ("0.90", "0.70", "Weak Match"),
    ("0.80", "0.70", "Strong Match"), ("0.90", "0.699999", "Uncertain Result"),
    ("0.60", "0.60", "Uncertain Result")])
def test_comparison_exact_boundaries(left, right, expected):
    result = compare_predictions(pred(left), pred(right))
    assert result["status"] == expected
    assert result["confidence_difference"] == float(abs(Decimal(left) - Decimal(right)))


def test_disagreement_missing_and_review_class():
    assert compare_predictions(pred(".82"), pred(".77", "invalid"))["status"] == "Model Disagreement"
    assert compare_predictions(pred(), None)["status"] == "Uncertain Result"
    assert compare_predictions(None, None)["models_agree"] is None
    assert compare_predictions(pred(".9", "manual_review"), pred(".9", "manual_review"))["status"] == "Uncertain Result"
    with pytest.raises(ValueError):
        ComparisonThresholds(strong="NaN")
    with pytest.raises(ValueError):
        ComparisonThresholds(strong=.2, acceptable=.1)


def outcome(evidence=None, python=None, gtm=None, duplicates=None, failures=None):
    evidence = evidence or valid_evidence()
    python, gtm = python or pred(), gtm or pred()
    rules = check_rules(evidence, Policy(**policy_dict()))
    return decide(rules, compare_predictions(python, gtm), python, gtm, duplicates or [], failures or []), rules


def test_valid_expiry_and_policy_precedence():
    evidence = valid_evidence()
    assert outcome(evidence)[0]["decision"] == "likely_valid"
    evidence["claim_date"] = "2025-03-01"
    decision, rules = outcome(evidence)
    assert decision["decision"] == "likely_invalid"
    assert "The policy warranty period has expired." in decision["problems"]
    assert next(r for r in rules if r["code"] == "WARRANTY_EXPIRED")["details"]["expiry"] == "2025-02-28"


def test_exclusive_final_day_and_timezone():
    policy = Policy(**{**policy_dict(), "final_day_inclusive": False})
    assert any(r["code"] == "WARRANTY_EXPIRED" for r in check_rules(valid_evidence(), policy))
    assert calendar_date("2025-03-01T00:30:00+01:00") == date(2025, 2, 28)
    assert calendar_date(datetime(2025, 2, 28, tzinfo=timezone.utc)) == date(2025, 2, 28)


@pytest.mark.parametrize("change,code", [
    ({"purchase_date": "2026-01-01"}, "DATE_CONTRADICTIONS"),
    ({"fault_date": "2026-01-01"}, "DATE_CONTRADICTIONS"),
    ({"purchase_date": None}, "INVALID_DATES"), ({"purchase_date": "invalid"}, "INVALID_DATES"),
    ({"repairs": [{"source": "repair", "repair_date": "2023-01-01", "authorized": True}]}, "DATE_CONTRADICTIONS"),
    ({"repairs": [{"source": "repair", "repair_date": "2024-08-01", "authorized": False}]}, "REPAIR_AUTHORIZATION"),
    ({"documents": []}, "REQUIRED_DOCUMENTS")])
def test_contradictions_missing_and_repair(change, code):
    decision, rules = outcome({**valid_evidence(), **change})
    assert decision["decision"] == "manual_review_required"
    assert any(r["code"] == code and r["result"] == "manual_review" for r in rules)


def test_serial_conflict_is_not_normalized_away():
    evidence = valid_evidence()
    evidence["serials"][1]["value"] = "ABC123"
    decision, rules = outcome(evidence)
    assert decision["decision"] == "manual_review_required"
    serial = next(r for r in rules if r["code"] == "SERIAL_CONFLICT")
    assert {r["source"] for r in serial["details"]["sources"]} == {"claim", "receipt"}
    assert evidence["serials"][0]["value"] == " ABC-123 "


def test_exclusion_duplicates_disagreement_and_infrastructure_failure():
    assert outcome({**valid_evidence(), "damage_type": "liquid_damage"})[0]["decision"] == "likely_invalid"
    assert outcome(duplicates=[{"kind": "document_sha256"}])[0]["decision"] == "manual_review_required"
    assert outcome(gtm=pred(".77", "invalid"))[0]["decision"] == "manual_review_required"
    evidence = {**valid_evidence(), "claim_date": "2026-01-01"}
    assert outcome(evidence, failures=["GTM_MODEL_FAILED"])[0]["decision"] == "manual_review_required"


def test_three_policies_validate_and_reload(tmp_path):
    path = tmp_path / "policies.json"
    policies = [policy_dict("TEST-12", 12), policy_dict("TEST-24", 24), policy_dict("TEST-36", 36)]
    path.write_text(json.dumps({"policies": policies}))
    for source in policies:
        loaded, digest = load_policy(path, source["policy_code"])
        assert loaded.months == source["months"] and len(digest) == 64
    policies[0]["months"] = 18
    path.write_text(json.dumps({"policies": policies}))
    assert load_policy(path, "TEST-12")[0].months == 18
    with pytest.raises(PolicyError, match="POLICY_NOT_FOUND"):
        load_policy(path, "missing")
    del policies[0]["required_documents"]
    path.write_text(json.dumps({"policies": policies}))
    with pytest.raises(PolicyError, match="POLICY_INVALID_OR_UNAVAILABLE"):
        load_policy(path, "TEST-12")
    path.write_text("policies: [invalid YAML")
    with pytest.raises(PolicyError):
        load_policy(path, "TEST-12")
    with pytest.raises(PolicyError):
        load_policy(tmp_path / "missing.json", "TEST-12")
