"""Real migrations, HTTP, JWTs and persistence with explicitly synthetic model providers."""
from copy import deepcopy
from datetime import date
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError
from backend.db.models import (AuditLog, Claim, ClaimEvaluation, Document, GTMPrediction,
    ModelVersion, PythonPrediction, Review, User)
from backend.extensions import db
from backend.services.claim_evaluation import model_features, snapshot
from backend.services.claim_storage import storage
from test_auth import app, client, accounts, claims, bearer, login
from test_evaluation_rules import policy_dict


class FixturePredictor:
    """Test provider, never registered by production code."""
    def __init__(self, version_id, kind):
        self.model_version_id, self.kind = version_id, kind
        self.scores = {"valid": .9, "invalid": .06, "manual_review": .04}

    def predict(self, features):
        assert "final_decision" not in features and "reviewer_decision" not in features
        return {"probabilities": dict(self.scores), "claim_summary_card_path": "cards/test.png"}


@pytest.fixture
def ready(app, claims, tmp_path):
    path = tmp_path / "policies.json"
    path.write_text(json.dumps({"policies": [policy_dict()]}))
    app.config["WARRANTY_POLICY_PATH"] = str(path)
    with app.app_context():
        claim = db.session.get(Claim, claims["customer"]["claim"])
        claim.product.purchase_date = date(2024, 2, 29)
        claim.product.serial_number = "ABC123"
        claim.warranty.start_date, claim.warranty.expiry_date = date(2024, 2, 29), date(2025, 2, 28)
        claim.fault_date, claim.submission_date = date(2025, 2, 20), date(2025, 2, 28)
        app.config["WARRANTY_POLICY_BINDINGS"] = {claim.warranty.warranty_id: "TEST-12"}
        for kind in ("receipt", "serial_number_image"):
            content = ("test document " + kind).encode()
            key = "evidence/" + kind
            storage().save(key, content, "image/png")
            db.session.add(Document(claim_id=claim.id, uploaded_by=claim.user_id,
                document_type=kind, original_filename=kind + ".png", stored_filename=kind,
                mime_type="image/png", file_size=len(content), storage_path=key,
                file_hash=hashlib.sha256(content).hexdigest(), ocr_status="completed", review_status="confirmed",
                extracted_data={"serial_number": {"value": "ABC123", "confidence": .95}}))
        storage().save("cards/test.png", b"fixture summary card", "image/png")
        providers = {}
        for kind in ("python", "gtm"):
            model = ModelVersion(model_type=kind, model_name="Fixture " + kind, version="test-v1",
                artifact_path="test-only", training_dataset_version="synthetic", is_active=True)
            db.session.add(model)
            db.session.flush()
            providers[kind] = FixturePredictor(model.id, kind)
        db.session.commit()
        app.extensions["prediction_providers"] = providers
        return claim.id, claim.version


def evaluate(client, ready, key="test-evaluation-1", role="employee"):
    claim_id, version = ready
    return client.post(f"/api/claims/{claim_id}/evaluate", headers=bearer(login(client, role)["access_token"]),
        json={"version": version, "idempotency_key": key})


def test_complete_valid_workflow_override_and_retry(client, app, ready):
    response = evaluate(client, ready)
    assert response.status_code == 201, response.json
    evaluation = response.json["evaluation"]
    assert evaluation["decision"] == "likely_valid"
    assert evaluation["comparison"]["status"] == "Strong Match"
    assert evaluate(client, ready).status_code == 200
    reviewer = bearer(login(client, "reviewer")["access_token"])
    queue = client.get("/api/review/manual", headers=reviewer)
    assert queue.json["total"] == 1
    claim_id, version = ready[0], response.json["claim"]["version"]
    stale = client.post(f"/api/review/{claim_id}/approve", headers=reviewer, json={"notes": "Checked", "version": version - 1})
    assert stale.status_code == 409
    result = client.post(f"/api/review/{claim_id}/reject", headers=reviewer,
        json={"notes": "Inspection establishes excluded damage.", "version": version})
    assert result.status_code == 200, result.json
    assert result.json["claim"]["status"] == "rejected"
    assert result.json["claim"]["final_decision"] == "likely_valid"
    assert client.get("/api/review/manual", headers=reviewer).json["total"] == 0
    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(ClaimEvaluation)) == 1
        assert db.session.scalar(select(func.count()).select_from(PythonPrediction)) == 1
        assert db.session.scalar(select(func.count()).select_from(GTMPrediction)) == 1
        action = db.session.scalar(select(Review))
        assert action.override_applied and action.previous_decision == "likely_valid"
        assert action.evaluation_id == evaluation["id"] and action.comments and action.reviewer_user_id
        stored = db.session.get(ClaimEvaluation, evaluation["id"])
        assert stored.decision == "likely_valid" and stored.evidence == evaluation["evidence"]
        assert db.session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "claim.evaluated")) == 1
        stored.decision = "likely_invalid"
        with pytest.raises(ValueError, match="append-only"):
            db.session.flush()
        db.session.rollback()


@pytest.mark.parametrize("scenario,expected", [
    ("expired", "likely_invalid"), ("disagreement", "manual_review_required"),
    ("serial", "manual_review_required"), ("missing_document", "manual_review_required"),
    ("duplicate", "manual_review_required"), ("gtm_failure", "manual_review_required"),
    ("both_failure", "manual_review_required"), ("policy_missing", "manual_review_required"),
    ("python_failure", "manual_review_required"), ("python_invalid_output", "manual_review_required"),
    ("storage_missing", "manual_review_required"), ("ocr_failed", "manual_review_required")])
def test_decision_scenarios(client, app, claims, ready, scenario, expected):
    with app.app_context():
        claim = db.session.get(Claim, ready[0])
        if scenario == "expired":
            claim.submission_date = date(2025, 3, 1)
        elif scenario == "serial":
            claim.documents[0].extracted_data = {"serial_number": {"value": "ABC128"}}
        elif scenario == "missing_document":
            db.session.delete(claim.documents[1])
        elif scenario == "duplicate":
            original = claim.documents[0]
            db.session.add(Document(claim_id=claims["other"]["claim"], uploaded_by=claim.user_id,
                document_type="receipt", original_filename="other.png", stored_filename="other.png",
                mime_type="image/png", file_size=1, storage_path="other.png", file_hash=original.file_hash))
        elif scenario == "storage_missing":
            storage().delete(claim.documents[0].storage_path)
        elif scenario == "ocr_failed":
            claim.documents[0].ocr_status = "failed"
        db.session.commit()
        ready = (claim.id, claim.version)
    if scenario == "disagreement":
        app.extensions["prediction_providers"]["gtm"].scores = {"valid": .1, "invalid": .77, "manual_review": .13}
    if scenario in {"gtm_failure", "both_failure"}:
        app.extensions["prediction_providers"].pop("gtm")
    if scenario == "both_failure":
        app.extensions["prediction_providers"].pop("python")
    if scenario == "python_failure":
        app.extensions["prediction_providers"].pop("python")
    if scenario == "python_invalid_output":
        app.extensions["prediction_providers"]["python"].scores = {"valid": float("nan"), "invalid": .1, "manual_review": .1}
    if scenario == "policy_missing":
        app.config["WARRANTY_POLICY_PATH"] = "missing-policy.json"
    response = evaluate(client, ready)
    assert response.status_code == 201, response.json
    assert response.json["evaluation"]["decision"] == expected
    if scenario == "gtm_failure":
        assert response.json["evaluation"]["predictions"]["python"] is not None
        assert response.json["evaluation"]["predictions"]["gtm"] is None
    if scenario in {"python_failure", "python_invalid_output"}:
        assert response.json["evaluation"]["predictions"]["python"] is None
        assert response.json["evaluation"]["predictions"]["gtm"] is not None
    if scenario == "expired":
        reviewer = bearer(login(client, "reviewer")["access_token"])
        override = client.post(f"/api/review/{ready[0]}/approve", headers=reviewer, json={"notes": "Verified extension."})
        assert override.status_code == 200
        assert override.json["claim"]["final_decision"] == "likely_invalid"


def test_evaluation_rbac_idor_and_sensitive_filtering(client, app, ready, claims):
    assert evaluate(client, ready, role="customer").status_code == 403
    assert evaluate(client, ready, role="reviewer").status_code == 403
    assert evaluate(client, (claims["other"]["claim"], 1)).status_code == 404
    assert client.get(f"/api/claims/{ready[0]}/evaluations").status_code == 401
    assert evaluate(client, ready).status_code == 201
    customer = bearer(login(client, "customer")["access_token"])
    other = bearer(login(client, "other")["access_token"])
    response = client.get(f"/api/claims/{ready[0]}/evaluations", headers=customer)
    assert response.status_code == 200
    payload = response.get_data(as_text=True)
    for secret in ("storage_path", "file_hash", "model_inputs", "serials", "duplicates", "password_hash"):
        assert secret not in payload
    assert client.get(f"/api/claims/{ready[0]}/evaluations", headers=other).status_code == 404
    assert client.get(f"/api/claims/{ready[0]}/reviews", headers=customer).status_code == 403


@pytest.mark.parametrize("data", [None, [], {"version": "1", "idempotency_key": "test-key"},
    {"version": -1, "idempotency_key": "test-key"}, {"version": 1, "idempotency_key": "x"},
    {"version": 1, "idempotency_key": "test-key", "decision": "likely_valid"}])
def test_evaluation_input_validation(client, ready, data):
    response = client.post(f"/api/claims/{ready[0]}/evaluate", headers=bearer(login(client, "employee")["access_token"]), json=data)
    assert response.status_code in {400, 415}


def test_target_temporal_and_cross_request_isolation(app, ready):
    with app.app_context():
        claim = db.session.get(Claim, ready[0])
        evidence, _ = snapshot(claim)
        before = model_features(claim, evidence)
        assert "damage_cause" not in before and "fault_category" not in before
        claim.final_decision = "likely_invalid"
        claim.status = "approved"
        assert model_features(claim, evidence) == before
        evidence["fault_date"] = "2099-01-01"
        with pytest.raises(ValueError):
            model_features(claim, evidence)
        provider = app.extensions["prediction_providers"]["python"]
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(provider.predict, [deepcopy(before) for _ in range(8)]))
        assert all(r == results[0] for r in results)
        assert not hasattr(provider, "current_claim")
        db.session.rollback()


def test_concurrent_claim_writers_conflict(app, ready):
    with app.app_context():
        with Session(db.engine) as first, Session(db.engine) as second:
            a, b = first.get(Claim, ready[0]), second.get(Claim, ready[0])
            a.status = "approved"
            first.commit()
            b.status = "rejected"
            with pytest.raises(StaleDataError):
                second.commit()


@pytest.mark.parametrize("failure_point,expected", [("audit", 500), ("duplicate_database", 503)])
def test_evaluation_transaction_rolls_back(client, app, ready, monkeypatch, failure_point, expected):
    def fail(*args, **kwargs):
        if failure_point == "duplicate_database":
            from sqlalchemy.exc import OperationalError
            raise OperationalError("query", {}, RuntimeError("simulated database outage"))
        raise RuntimeError("simulated audit failure")
    monkeypatch.setattr("backend.api.evaluations.audit" if failure_point == "audit" else
        "backend.services.claim_evaluation.find_duplicates", fail)
    assert evaluate(client, ready).status_code == expected
    with app.app_context():
        for model in (ClaimEvaluation, PythonPrediction, GTMPrediction):
            assert db.session.scalar(select(func.count()).select_from(model)) == 0
        assert db.session.get(Claim, ready[0]).status == "submitted"


def test_simultaneous_reviewers_finalize_once(client, app, ready, monkeypatch):
    from threading import Barrier
    from backend.api import review
    result = evaluate(client, ready)
    version = result.json["claim"]["version"]
    headers = [bearer(login(client, role)["access_token"]) for role in ("reviewer", "admin")]
    original = review.reviewable
    barrier = Barrier(2)

    def synchronized(claim_id):
        claim = original(claim_id)
        barrier.wait(timeout=15)
        return claim

    monkeypatch.setattr(review, "reviewable", synchronized)

    def act(pair):
        action, header = pair
        with app.test_client() as worker:
            return worker.post(f"/api/review/{ready[0]}/{action}", headers=header,
                json={"version": version, "notes": "Independent evidence review"}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(act, zip(("approve", "reject"), headers)))
    assert sorted(statuses) == [200, 409]
    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(Review)) == 1


def test_old_document_route_respects_reviewer_assignment(client, app, ready, accounts):
    with app.app_context():
        claim = db.session.get(Claim, ready[0])
        claim.status, claim.manual_review_required = "manual_review", True
        # Assign to an unrelated reviewer identity; the caller must not see this file.
        second = User(email="private-reviewer@example.com", first_name="Private", last_name="Reviewer",
            full_name="Private Reviewer", role="reviewer", password_hash="not-used")
        db.session.add(second)
        db.session.flush()
        claim.assigned_reviewer_id = second.id
        document_id = claim.documents[0].id
        db.session.commit()
    reviewer = bearer(login(client, "reviewer")["access_token"])
    assert client.get(f"/api/claims/{ready[0]}/documents/{document_id}", headers=reviewer).status_code == 404
    assert client.get(f"/api/documents/{document_id}/content", headers=reviewer).status_code == 404


def test_json_nonfinite_numbers_never_escape(client, app, ready):
    from backend.json_provider import StrictJSONProvider
    with app.app_context():
        with pytest.raises(ValueError):
            app.json.dumps({"confidence": float("nan")})
    header = bearer(login(client, "employee")["access_token"])
    response = client.post(f"/api/claims/{ready[0]}/evaluate", headers=header,
        content_type="application/json", data='{"version": NaN, "idempotency_key": "test-key"}')
    assert response.status_code == 400


def test_dashboard_never_pairs_predictions_from_different_evaluations(client, app, ready):
    first = evaluate(client, ready)
    assert first.status_code == 201
    app.extensions["prediction_providers"].pop("gtm")
    second = evaluate(client, (ready[0], first.json["claim"]["version"]), key="second-evaluation")
    assert second.status_code == 201 and second.json["evaluation"]["predictions"]["gtm"] is None
    admin = bearer(login(client, "admin")["access_token"])
    summary = client.get("/api/dashboard/admin", headers=admin)
    assert summary.status_code == 200, summary.json
    assert summary.json["disagreement"]["processed"] == 0


def test_http_submission_evidence_evaluation_and_review_trace(client, app, ready, claims, accounts):
    from test_claim_submission import complete, submit
    customer = bearer(login(client, "customer")["access_token"])
    draft = complete(client, customer, claims["customer"]["product"])
    submitted = submit(client, customer, draft)
    assert submitted.status_code == 201, submitted.json
    claim_id = submitted.json["claim"]["id"]
    admin = bearer(login(client, "admin")["access_token"])
    assigned = client.patch(f"/api/claims/{claim_id}/assignment", headers=admin,
        json={"employee_id": accounts["employee"]})
    assert assigned.status_code == 200
    evaluated = evaluate(client, (claim_id, assigned.json["claim"]["version"]), key="full-http-workflow")
    assert evaluated.status_code == 201, evaluated.json
    machine = evaluated.json["evaluation"]
    assert machine["decision"] == "manual_review_required"
    assert machine["predictions"]["python"] and machine["predictions"]["gtm"]
    assert len(machine["evidence"]["documents"]) == 4
    assert all(d["integrity_verified"] for d in machine["evidence"]["documents"])
    reviewer = bearer(login(client, "reviewer")["access_token"])
    queue = client.get("/api/review/manual", headers=reviewer).json["items"]
    assert any(c["id"] == claim_id for c in queue)
    asked = client.post(f"/api/review/{claim_id}/request-information", headers=reviewer,
        json={"notes": "Provide readable serial evidence.", "version": evaluated.json["claim"]["version"]})
    assert asked.status_code == 200 and asked.json["claim"]["status"] == "additional_information_required"
    assert client.post(f"/api/dashboard/reviewer/claims/{claim_id}/resume", headers=reviewer).status_code == 200
    approved = client.post(f"/api/review/{claim_id}/approve", headers=reviewer,
        json={"notes": "Manual inspection confirms coverage and legitimate evidence reuse."})
    assert approved.status_code == 200 and approved.json["claim"]["status"] == "approved"
    assert approved.json["claim"]["final_decision"] == "manual_review_required"
    with app.app_context():
        stored = db.session.get(ClaimEvaluation, machine["id"])
        assert stored.evidence == machine["evidence"]
        actions = db.session.scalars(select(Review).where(Review.claim_id == claim_id).order_by(Review.id)).all()
        assert [a.decision for a in actions] == ["request_information", "approve"]
        assert all(a.evaluation_id == stored.id for a in actions)
        assert actions[-1].override_applied and actions[-1].comments
        audits = set(db.session.scalars(select(AuditLog.action).where(AuditLog.claim_id == claim_id)))
        assert {"claim.evaluated", "review.request_information", "review.approve"} <= audits
