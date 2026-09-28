"""Temporary probe 3 - delete before finishing. Real API end-to-end evaluation."""
from datetime import date
import pytest
from backend.db.models import Claim, Document, RuleResult
from backend.extensions import db
from backend.services.claim_rules import ClaimRuleEngine
from backend.services.warranty_policy import WarrantyPolicyService
from test_auth import app, client, accounts, claims, bearer, login  # noqa: F401
from test_claim_submission import draft, complete, submit, png


def fake(kind, predicted, top):
    rest = round((1 - top) / 2, 6)
    scores = {k: rest for k in ("valid", "invalid", "manual_review")}
    scores[predicted] = top
    class Service:
        def predict(self, claim, policy):
            return {"prediction": predicted, "prediction_class": predicted, "confidence": scores,
                    "top_confidence": top, "model": kind, "model_version": "probe",
                    "inference_duration_ms": 1}, None
    return Service


@pytest.fixture
def injected(monkeypatch):
    monkeypatch.setattr("backend.services.evaluation.PythonPredictionService", fake("python", "valid", .9))
    monkeypatch.setattr("backend.services.evaluation.GTMPredictionService", fake("gtm", "valid", .9))


def test_probe_real_pipeline(client, app, claims, accounts, injected, monkeypatch):
    headers = bearer(login(client)["access_token"])
    claim = complete(client, headers, claims["customer"]["product"])
    response = submit(client, headers, claim)
    assert response.status_code == 201, response.json
    public_id = response.json["claim"]["claim_id"]
    with app.app_context():
        claim_id = db.session.scalar(db.session.query(Claim).filter_by(claim_id=public_id).with_entities(Claim.id))
        print("\nDOC TYPES:", [d.document_type for d in db.session.get(Claim, claim_id).documents])
        print("OCR STATUS:", [(d.document_type, d.ocr_status, d.review_status,
              bool(d.extracted_data), bool(d.verified_data)) for d in db.session.get(Claim, claim_id).documents])
        policy = WarrantyPolicyService(app.config["WARRANTY_POLICY_PATH"]).for_product(db.session.get(Claim, claim_id).product)
        print("POLICY required:", policy.required_documents)
        rules, contradictions = ClaimRuleEngine().evaluate(db.session.get(Claim, claim_id), policy)
        print("RULES:", {r["rule_code"]: r["result"] for r in rules})
        print("MISSING EVIDENCE:", [r["evidence"] for r in rules if r["rule_code"] == "missing_documents"])

    reviewer = bearer(login(client, "reviewer")["access_token"])
    result = client.post(f"/api/claims/{claim_id}/evaluate", headers=reviewer)
    assert result.status_code == 201, result.json
    evaluation = result.json["evaluation"]
    print("\nRECOMMENDATION:", evaluation["recommendation"], evaluation["status"])
    print("EXPLANATION:", evaluation["explanation"])
    print("COMPARISON:", evaluation["comparison"])
    print("DUP:", evaluation["duplicate_finding"]["duplicate_risk"], evaluation["duplicate_finding"]["score"])
    print("CONTRADICTIONS:", [c["type"] for c in evaluation["contradictions"]])
    print("RULE RESULTS:", [(r.rule_code, r.result) for r in db.session.scalars(
        __import__("sqlalchemy").select(RuleResult).where(RuleResult.claim_id == claim_id)).all()] if False else "")
    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        print("CLAIM STATUS:", claim.status, "MANUAL:", claim.manual_review_required, "FINAL:", claim.final_decision)
        print("STORED RULES:", [(r.rule_code, r.result) for r in
              __import__("sqlalchemy").select(RuleResult).where(RuleResult.claim_id == claim_id)])


def test_probe_serial_from_real_ocr(client, app, claims, ocr):
    """Confirmed OCR review values - does the rule engine read them correctly?"""
    from test_document_ocr import image_bytes, upload_new
    headers = bearer(login(client)["access_token"])
    claim = draft(client, headers, claims["customer"]["product"],
        fault_date=date.today().isoformat(), fault_type="Electrical Failure",
        description="The screen no longer turns on when connected to power.", damage_category="Moderate")
    uploaded = upload_new(client, headers, claim, content=image_bytes("PNG", "teal"), kind="receipt",
        filename="receipt.png")
    document, claim = uploaded.json["document"], uploaded.json["claim"]
    reviewed = client.patch(f"/api/documents/{document['id']}/ocr-review", headers=headers,
        json={"version": claim["version"], "confirm": True})
    claim = reviewed.json["claim"]
    with app.app_context():
        stored = db.session.get(Document, document["id"])
        print("\nEXTRACTED serial:", stored.extracted_data.get("serial_number"))
        print("VERIFIED serial:", (stored.verified_data or {}).get("serial_number"))
        claim_row = db.session.get(Claim, claim["id"])
        claim_row.submission_date = date.today()
        db.session.commit()
        policy = WarrantyPolicyService(app.config["WARRANTY_POLICY_PATH"]).for_product(claim_row.product)
        rules, contradictions = ClaimRuleEngine().evaluate(claim_row, policy)
        print("RULES:", {r["rule_code"]: r["result"] for r in rules})
        print("SERIAL EVIDENCE:", [r["evidence"] for r in rules if r["rule_code"] == "serial_number_verification"][0])
