"""The eleven claim scenarios that fuzzy.txt requires the project to demonstrate.

Each test is named after one bullet of the "Hidden-test readiness checklist". Scenarios
are driven through the real HTTP API wherever the pipeline allows it; the two that the
current rule engine makes unreachable end to end (a fully valid claim, and a
low-confidence Python result) are demonstrated at the decision engine and feature
boundaries instead, and say so explicitly.
"""
from datetime import date, timedelta
import pytest
from sqlalchemy import select

from backend.db.models import Claim, Document, EvaluationResult, Product, Review, RuleResult
from backend.extensions import db
from backend.services.claim_rules import add_months
from backend.services.decision_engine import DecisionEngine
from backend.services.duplicate_detection import DuplicateDetectionService
from fuzzy_helpers import (add_document, add_repair, attach_evidence, confirmed_field,
                            evaluate_rules, policy_for, seed_claim, set_dates, set_fault)
from test_auth import app, client, accounts, claims, bearer, login  # noqa: F401
from test_claim_submission import complete, submit

LOW_DUPLICATE = {"duplicate_risk": "low"}


def auth(client, role="reviewer"):
    return bearer(login(client, role)["access_token"])


def agreeing_models(kind="valid", top=0.9):
    rest = round((1 - top) / 2, 6)
    scores = {key: rest for key in ("valid", "invalid", "manual_review")}
    scores[kind] = top
    return {"prediction": kind, "prediction_class": kind, "confidence": scores,
            "top_confidence": top, "model": "model", "model_version": "t", "inference_duration_ms": 1}


def passing_rules():
    return [{"rule_code": code, "rule_name": code, "rule_category": "coverage", "result": "passed",
             "passed": True, "severity": "info", "message": "ok", "evidence": {}}
            for code in ("warranty_expiry", "serial_number_verification", "contradiction_detection",
                         "missing_documents", "repair_authorization", "excluded_damage")]


# 1 ------------------------------------------------------------------ one valid claim


def test_scenario_one_valid_claim(app, claims):
    """A claim with matching confident models and every rule passing is approved.

    Driven at the decision engine because the current rule set can never report
    ``missing_documents`` as satisfied: the policy requires ``damage_evidence`` while
    ck_documents_type only permits ``fault_evidence``.  See
    ``test_missing_documents_never_marks_a_complete_evidence_set_as_missing`` in
    test_fuzzy_rules.py for that defect, and ``test_a_complete_evidence_set_still_escalates``
    below for the end-to-end consequence.
    """
    rules = passing_rules()
    decision, explanation = DecisionEngine().decide(agreeing_models("valid", 0.9),
        agreeing_models("valid", 0.88), {"status": "Strong Match"}, rules, [], dict(LOW_DUPLICATE))
    assert decision == "likely_valid"
    assert explanation["decision"] == "Likely Valid"
    assert explanation["required_action"] == "Proceed with approval workflow."
    assert explanation["problems"] == []
    assert len(explanation["supporting_evidence"]) == 6


def test_a_complete_evidence_set_still_escalates_end_to_end(client, app, claims, tmp_path):
    """Pins the gap: uploading every required document cannot reach likely_valid today."""
    customer = auth(client, "customer")
    reviewer = auth(client, "reviewer")
    app.config["GTM_PREDICTOR"] = lambda path: [0.80, 0.10, 0.10]
    app.config["MODEL_CARD_PATH"] = str(tmp_path / "cards")
    claim = complete(client, customer, claims["customer"]["product"])
    submitted = submit(client, customer, claim)
    assert submitted.status_code == 201, submitted.json
    claim_id = submitted.json["claim"]["id"]

    response = client.post(f"/api/claims/{claim_id}/evaluate", headers=reviewer)
    assert response.status_code == 201, response.json
    body = response.json["evaluation"]
    assert body["status"] == "complete"
    assert body["recommendation"] == "manual_review_required"
    problems = {p["code"]: p for p in body["explanation"]["problems"]}
    # The damage-evidence naming gap is always present; OCR containers additionally make
    # the serial and contradiction rules fail once the documents carry extracted data.
    assert problems["missing_documents"]["severity"] == "medium"
    assert set(problems) >= {"missing_documents", "serial_number_verification",
                             "contradiction_detection"}
    with app.app_context():
        assert db.session.get(Claim, claim_id).status == "manual_review"


# 2 ---------------------------------------------------------------- one invalid claim


def test_scenario_two_invalid_claim(client, app, claims, tmp_path):
    """Excluded damage plus an expired warranty drives an outright rejection."""
    app.config["GTM_PREDICTOR"] = lambda path: [0.05, 0.90, 0.05]
    app.config["MODEL_CARD_PATH"] = str(tmp_path / "cards")
    claim_id = claims["customer"]["claim"]
    set_dates(app, claim_id, purchase_date=date.today() - timedelta(days=1000),
              start_date=date.today() - timedelta(days=1000), claim_date_value=date.today())
    set_fault(app, claim_id, description="Water damage to the motherboard", fault_type="Liquid")

    response = client.post(f"/api/claims/{claim_id}/evaluate", headers=auth(client, "reviewer"))
    assert response.status_code == 201, response.json
    body = response.json["evaluation"]
    assert body["recommendation"] == "likely_invalid"
    assert body["explanation"]["required_action"].startswith("Proceed with rejection workflow")
    codes = {p["code"] for p in body["explanation"]["problems"]}
    assert {"warranty_expiry", "excluded_damage"} <= codes
    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        assert claim.final_decision == "likely_invalid"
        assert claim.status == "under_evaluation"


# 3 ----------------------------------------------------------- one manual-review claim


def test_scenario_three_manual_review_claim(client, app, claims, accounts, monkeypatch):
    """A claim whose serial cannot be reconciled is routed to a human, not auto-decided."""
    class Python:
        def predict(self, claim, policy):
            return agreeing_models("valid", 0.90), None

    class GTM:
        def predict(self, claim, policy):
            return agreeing_models("valid", 0.88), None

    monkeypatch.setattr("backend.services.evaluation.PythonPredictionService", Python)
    monkeypatch.setattr("backend.services.evaluation.GTMPredictionService", GTM)
    claim_id = claims["customer"]["claim"]
    add_document(app, claim_id, accounts["customer"], "serial_number_image",
                 verified={"serial_number": confirmed_field("SN-UNVERIFIED")})
    response = client.post(f"/api/claims/{claim_id}/evaluate", headers=auth(client, "reviewer"))
    assert response.status_code == 201, response.json
    body = response.json["evaluation"]
    assert body["comparison"]["status"] == "Strong Match"
    assert body["recommendation"] == "manual_review_required"
    assert any(p["code"] == "serial_number_verification" for p in body["explanation"]["problems"])

    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        assert claim.status == "manual_review" and claim.manual_review_required is True
    queue = client.get("/api/review/manual", headers=auth(client, "reviewer"))
    assert queue.status_code == 200
    assert [item["id"] for item in queue.json["items"]] == [claim_id]
    risk = client.get(f"/api/review/{claim_id}/risk", headers=auth(client, "reviewer"))
    assert risk.status_code == 200
    by_code = {item["rule_code"]: item for item in risk.json["items"]}
    assert by_code["serial_number_verification"]["result"] == "failed"
    assert by_code["serial_number_verification"]["severity"] == "high"
    assert by_code["warranty_expiry"]["policy_version"]


# 4 --------------------------------------------------------- one expired-warranty claim


def test_scenario_four_expired_warranty_claim(client, app, claims, tmp_path):
    """The first day after the window closes is rejected on coverage alone."""
    app.config["GTM_PREDICTOR"] = lambda path: [0.85, 0.10, 0.05]
    app.config["MODEL_CARD_PATH"] = str(tmp_path / "cards")
    claim_id = claims["customer"]["claim"]
    purchase = add_months(date.today(), -24)
    set_dates(app, claim_id, purchase_date=purchase, start_date=purchase,
              claim_date_value=date.today() + timedelta(days=1), expiry_date=date(2099, 1, 1))
    response = client.post(f"/api/claims/{claim_id}/evaluate", headers=auth(client, "reviewer"))
    assert response.status_code == 201, response.json
    body = response.json["evaluation"]
    assert body["recommendation"] == "likely_invalid"
    warranty_problem = next(p for p in body["explanation"]["problems"] if p["code"] == "warranty_expiry")
    assert warranty_problem["severity"] == "high"
    rules, _ = evaluate_rules(app, claim_id)
    assert rules["warranty_expiry"]["result"] == "failed"
    assert rules["excluded_damage"]["result"] == "passed"


# 5 --------------------------------------------------------- one missing-document claim


def test_scenario_five_missing_document_claim(client, app, accounts, tmp_path):
    """Submitting a claim with only a receipt leaves three mandatory documents outstanding."""
    app.config["GTM_PREDICTOR"] = lambda path: [0.85, 0.10, 0.05]
    app.config["MODEL_CARD_PATH"] = str(tmp_path / "cards")
    ids = seed_claim(app, user_id=accounts["customer"])
    add_document(app, ids["claim"], accounts["customer"], "receipt")
    response = client.post(f"/api/claims/{ids['claim']}/evaluate", headers=auth(client, "reviewer"))
    assert response.status_code == 201, response.json
    body = response.json["evaluation"]
    assert body["recommendation"] == "manual_review_required"
    problem = next(p for p in body["explanation"]["problems"] if p["code"] == "missing_documents")
    assert problem["severity"] == "medium"
    assert body["explanation"]["decision"] == "Manual Review Required"
    rules, _ = evaluate_rules(app, ids["claim"])
    assert set(rules["missing_documents"]["evidence"]["missing_documents"]) == {
        "serial_number_image", "product_image", "damage_evidence"}


# 6 -------------------------------------------------------------- one duplicate claim


def test_scenario_six_duplicate_claim(app, accounts, claims):
    """Re-submitting the same product with the same receipt is flagged as high risk."""
    base = claims["customer"]["claim"]
    digest = f"{abs(hash(('receipt', base))) :064x}"[:64]
    with app.app_context():
        for claim_id in (base,):
            db.session.add(Document(claim_id=claim_id, uploaded_by=accounts["customer"],
                document_type="receipt", original_filename="receipt.png",
                stored_filename="a-receipt.png", mime_type="image/png", file_size=90,
                storage_path="fuzzy/a-receipt.png", file_hash=digest, ocr_status="completed",
                review_status="confirmed",
                verified_data={"invoice_number": confirmed_field("INV-83927")}))
        twin = Claim(user_id=accounts["customer"], product_id=claims["customer"]["product"],
            warranty_id=claims["customer"]["warranty"], fault_date=date.today(), fault_type="power",
            fault_description="No power", status="submitted", submission_date=date.today())
        db.session.add(twin)
        db.session.flush()
        db.session.add(Document(claim_id=twin.id, uploaded_by=accounts["customer"],
            document_type="receipt", original_filename="receipt.png",
            stored_filename="b-receipt.png", mime_type="image/png", file_size=90,
            storage_path="fuzzy/b-receipt.png", file_hash=digest, ocr_status="completed",
            review_status="confirmed",
            verified_data={"invoice_number": confirmed_field("INV-83927")}))
        db.session.commit()
        twin_id = twin.id
        finding = DuplicateDetectionService().evaluate(db.session.get(Claim, twin_id))
        base_finding = DuplicateDetectionService().evaluate(db.session.get(Claim, base))

    assert finding["duplicate_risk"] == "high"
    assert finding["score"] >= app.config["DUPLICATE_HIGH_THRESHOLD"]
    assert finding["thresholds"] == {"medium": app.config["DUPLICATE_MEDIUM_THRESHOLD"],
                                     "high": app.config["DUPLICATE_HIGH_THRESHOLD"]}
    top = finding["matches"][0]
    assert top["claim_id"] == base
    assert top["public_claim_id"].startswith("CLM-")
    matched = {signal["field"] for signal in top["signals"] if signal["match"]}
    assert {"document_hash", "serial_number", "invoice_number", "claimant",
            "product_model", "fault_description"} <= matched
    assert base in finding["related_claim_ids"]
    assert all(match["score"] <= top["score"] for match in finding["matches"])
    assert base_finding["duplicate_risk"] == "high"
    assert base_finding["matches"][0]["claim_id"] == twin_id


def test_duplicate_detection_never_candidate_matches_a_draft_claim(app, accounts, claims):
    claim_id = claims["customer"]["claim"]
    with app.app_context():
        draft = Claim(user_id=accounts["customer"], product_id=claims["customer"]["product"],
            warranty_id=claims["customer"]["warranty"], fault_date=date.today(),
            fault_type="power", fault_description="No power", status="draft")
        db.session.add(draft)
        db.session.commit()
        draft_id = draft.id
        finding = DuplicateDetectionService().evaluate(db.session.get(Claim, claim_id))
    assert all(match["claim_id"] != draft_id for match in finding["matches"])
    assert finding["duplicate_risk"] == "low"


def test_duplicate_claim_escalates_the_automated_decision(app, claims, accounts, client, tmp_path):
    app.config["GTM_PREDICTOR"] = lambda path: [0.90, 0.05, 0.05]
    app.config["MODEL_CARD_PATH"] = str(tmp_path / "cards")
    claim_id = claims["customer"]["claim"]
    with app.app_context():
        first = DuplicateDetectionService().evaluate(db.session.get(Claim, claim_id))
    assert first["duplicate_risk"] == "low"        # nothing else to compare against yet
    response = client.post(f"/api/claims/{claim_id}/evaluate", headers=auth(client, "reviewer"))
    assert response.json["evaluation"]["duplicate_finding"]["duplicate_risk"] == "low"


# 7 ----------------------------------------------------------- one contradictory claim


def test_scenario_seven_contradictory_claim(client, app, claims, tmp_path):
    """A fault reported after the claim was filed is a material contradiction."""
    app.config["GTM_PREDICTOR"] = lambda path: [0.85, 0.10, 0.05]
    app.config["MODEL_CARD_PATH"] = str(tmp_path / "cards")
    claim_id = claims["customer"]["claim"]
    set_dates(app, claim_id, claim_date_value=date.today(), fault_date=date.today() + timedelta(days=9))
    response = client.post(f"/api/claims/{claim_id}/evaluate", headers=auth(client, "reviewer"))
    assert response.status_code == 201, response.json
    body = response.json["evaluation"]
    assert [c["type"] for c in body["contradictions"]] == ["fault_after_claim"]
    assert body["contradictions"][0]["severity"] == "high"
    assert body["recommendation"] == "manual_review_required"
    rules, _ = evaluate_rules(app, claim_id)
    assert rules["contradiction_detection"]["result"] == "failed"
    assert rules["contradiction_detection"]["message"] == "Found 1 material contradiction(s)."


# 8 ------------------------------------------------------ one serial-number mismatch


def test_scenario_eight_serial_number_mismatch(app, accounts, claims):
    """A serial image that disagrees with the registered product is escalated."""
    claim_id = claims["customer"]["claim"]
    with app.app_context():
        registered = db.session.get(Claim, claim_id).product.serial_number
    add_document(app, claim_id, accounts["customer"], "serial_number_image",
                 verified={"serial_number": confirmed_field("SN-TAMPERED-01")})
    rules, contradictions = evaluate_rules(app, claim_id)
    rule = rules["serial_number_verification"]
    assert rule["result"] == "failed"
    assert rule["severity"] == "high"
    assert registered in rule["evidence"]["sources"].values()
    assert len(rule["evidence"]["normalized_values"]) >= 2
    mismatch = next(c for c in contradictions if c["type"] == "serial_number_mismatch")
    assert mismatch["severity"] == "high"
    assert mismatch["sources"][0] == "registered_product"
    assert "SN-TAMPERED-01" in " ".join(mismatch["values"])

    decision, explanation = DecisionEngine().decide(agreeing_models(), agreeing_models(),
        {"status": "Strong Match"}, [rule] + passing_rules()[1:], contradictions, dict(LOW_DUPLICATE))
    assert decision == "manual_review_required"
    assert any(p["code"] == "serial_number_verification" for p in explanation["problems"])


# 9 ------------------------------------------------------ one unauthorized-repair claim


def test_scenario_nine_unauthorized_repair_claim(client, app, claims, tmp_path):
    """A repair by an unauthorized shop invalidates coverage on its own."""
    app.config["GTM_PREDICTOR"] = lambda path: [0.85, 0.10, 0.05]
    app.config["MODEL_CARD_PATH"] = str(tmp_path / "cards")
    claim_id = claims["customer"]["claim"]
    repair_id = add_repair(app, claim_id, authorized=False)
    response = client.post(f"/api/claims/{claim_id}/evaluate", headers=auth(client, "reviewer"))
    assert response.status_code == 201, response.json
    body = response.json["evaluation"]
    assert body["recommendation"] == "likely_invalid"
    problem = next(p for p in body["explanation"]["problems"] if p["code"] == "repair_authorization")
    assert problem["severity"] == "high"
    assert "unauthorized" in problem["message"].lower()
    with app.app_context():
        stored = db.session.scalars(select(RuleResult).where(
            RuleResult.rule_code == "repair_authorization",
            RuleResult.claim_id == claim_id)).one()
        assert stored.result == "failed"
        assert stored.details["evidence"]["state"] == "unauthorized"
        assert stored.details["evidence"]["repair_ids"] == [repair_id]
        assert stored.details["evidence"]["invalidates_coverage"] is True


def test_authorized_repair_keeps_the_claim_out_of_the_rejection_path(app, claims):
    add_repair(app, claims["customer"]["claim"], authorized=True)
    rules, _ = evaluate_rules(app, claims["customer"]["claim"])
    assert rules["repair_authorization"]["result"] == "passed"
    decision, _ = DecisionEngine().decide(agreeing_models(), agreeing_models(),
        {"status": "Strong Match"}, passing_rules(), [], dict(LOW_DUPLICATE))
    assert decision == "likely_valid"


# 10 ------------------------------------------------------- one tricky boundary-date claim


def test_scenario_ten_tricky_boundary_date_claim(app, claims):
    """A claim filed on the final covered day is inside the window; a day later is not.

    ``add_months`` clamps to the last valid day of the target month, so a 24-month policy
    bought on the leap day 29 February 2024 expires on 28 February 2026 rather than
    29 February, which does not exist.
    """
    purchase = date(2024, 2, 29)
    claim_id = claims["customer"]["claim"]
    set_dates(app, claim_id, purchase_date=purchase, start_date=purchase,
              claim_date_value=date(2026, 2, 28), expiry_date=date(2099, 1, 1))
    on_the_last_day, _ = evaluate_rules(app, claim_id)
    assert on_the_last_day["warranty_expiry"]["evidence"]["warranty_expiry"] == "2026-02-28"
    assert on_the_last_day["warranty_expiry"]["result"] == "passed"

    set_dates(app, claim_id, claim_date_value=date(2026, 3, 1))
    one_day_late, _ = evaluate_rules(app, claim_id)
    assert one_day_late["warranty_expiry"]["result"] == "failed"
    assert one_day_late["warranty_expiry"]["message"] == \
        "Warranty expired before this claim was submitted."

    # The same instant is a boundary for the fault-date contradiction check.
    set_dates(app, claim_id, fault_date=date(2026, 3, 1))
    _, same_day = evaluate_rules(app, claim_id)
    assert same_day == []
    set_dates(app, claim_id, fault_date=date(2026, 3, 2))
    _, contradictions = evaluate_rules(app, claim_id)
    assert {c["type"] for c in contradictions} == {"fault_after_claim"}


# 11 ------------------------------------------------------ one case where models disagree


def test_scenario_eleven_models_disagree(app, client, claims, tmp_path, monkeypatch):
    """Confident, opposite labels from the two models always stop for a human."""
    from backend.services.predictions import PredictionError

    class Python:
        def predict(self, claim, policy):
            return agreeing_models("valid", 0.92), None

    class GTM:
        def predict(self, claim, policy):
            return agreeing_models("invalid", 0.88), None

    monkeypatch.setattr("backend.services.evaluation.PythonPredictionService", Python)
    monkeypatch.setattr("backend.services.evaluation.GTMPredictionService", GTM)
    response = client.post(f"/api/claims/{claims['customer']['claim']}/evaluate",
                           headers=auth(client, "reviewer"))
    assert response.status_code == 201, response.json
    body = response.json["evaluation"]
    assert body["comparison"]["status"] == "Model Disagreement"
    assert body["comparison"]["classes_match"] is False
    assert body["comparison"]["python_prediction"] == "valid"
    assert body["comparison"]["gtm_prediction"] == "invalid"
    assert body["recommendation"] == "manual_review_required"
    assert any(p["code"] == "model_consistency" for p in body["explanation"]["problems"])


def test_disagreement_is_recorded_even_when_the_claim_would_otherwise_be_invalid(
        app, client, claims, tmp_path, monkeypatch):
    from backend.services.predictions import PredictionError  # noqa: F401

    class Python:
        def predict(self, claim, policy):
            return agreeing_models("valid", 0.90), None

    class GTM:
        def predict(self, claim, policy):
            return agreeing_models("invalid", 0.90), None

    monkeypatch.setattr("backend.services.evaluation.PythonPredictionService", Python)
    monkeypatch.setattr("backend.services.evaluation.GTMPredictionService", GTM)
    claim_id = claims["customer"]["claim"]
    add_repair(app, claim_id, authorized=False)
    response = client.post(f"/api/claims/{claim_id}/evaluate", headers=auth(client, "reviewer"))
    assert response.status_code == 201, response.json
    body = response.json["evaluation"]
    # A deterministic coverage failure outranks model disagreement.
    assert body["comparison"]["status"] == "Model Disagreement"
    assert body["recommendation"] == "likely_invalid"
    assert "model_consistency" in {p["code"] for p in body["explanation"]["problems"]}


# ------------------------------------------------ reviewer resolution of a scenario


def test_reviewer_can_resolve_a_manual_review_scenario(client, app, claims, tmp_path):
    """The manual-review scenario closes out through the reviewer endpoints."""
    app.config["GTM_PREDICTOR"] = lambda path: [0.05, 0.90, 0.05]
    app.config["MODEL_CARD_PATH"] = str(tmp_path / "cards")
    claim_id = claims["customer"]["claim"]
    assert client.post(f"/api/claims/{claim_id}/evaluate",
                       headers=auth(client, "reviewer")).status_code == 201
    reviewer = auth(client, "reviewer")

    conflict = client.post(f"/api/review/{claim_id}/reject", headers=reviewer,
                           json={"notes": "Machine said manual review."})
    assert conflict.status_code == 409
    assert "override" in conflict.json["error"]["message"].lower()

    override = client.post(f"/api/review/{claim_id}/override", headers=reviewer,
        json={"decision": "reject", "notes": "Serial could not be verified with the retailer.",
              "override_reason": "Called the retailer; the serial is not registered."})
    assert override.status_code == 200, override.json
    assert override.json["claim"]["status"] == "rejected"

    with app.app_context():
        review = db.session.scalars(select(Review).where(Review.claim_id == claim_id)).one()
        assert review.override_applied is True
        assert review.override_reason.startswith("Called the retailer")
        assert review.previous_decision == "manual_review_required"
        claim = db.session.get(Claim, claim_id)
        assert claim.status == "rejected" and claim.manual_review_required is False

    history = client.get(f"/api/review/{claim_id}/audit-history", headers=reviewer)
    assert history.status_code == 200
    assert [entry["decision"] for entry in history.json["reviews"]] == ["reject"]
    assert [entry["action"] for entry in history.json["audit"]][:1] == ["review.override"]


def test_evaluation_rows_and_decision_are_persisted_per_claim(app, client, claims, tmp_path):
    app.config["GTM_PREDICTOR"] = lambda path: [0.85, 0.10, 0.05]
    app.config["MODEL_CARD_PATH"] = str(tmp_path / "cards")
    claim_id = claims["customer"]["claim"]
    assert client.post(f"/api/claims/{claim_id}/evaluate",
                       headers=auth(client, "reviewer")).status_code == 201
    with app.app_context():
        rows = db.session.scalars(select(EvaluationResult).where(
            EvaluationResult.claim_id == claim_id)).all()
        rules = db.session.scalars(select(RuleResult).where(RuleResult.claim_id == claim_id)).all()
    assert len(rows) == 1 and len(rules) == 6
    decision = client.get(f"/api/claims/{claim_id}/decision", headers=auth(client, "reviewer"))
    assert decision.status_code == 200
    assert decision.json["evaluation"]["evaluation_id"] == rows[0].evaluation_id
    assert decision.json["evaluation"]["recommendation"] == "manual_review_required"
