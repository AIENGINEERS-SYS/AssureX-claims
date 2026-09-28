"""Authentication, authorization, input-validation and injection tests for the new
prediction, evaluation, decision and review surfaces.

Covers the "Security test cases" and "Negative test cases" sections of fuzzy.txt.
"""
import pytest

from backend.db.models import Claim
from backend.extensions import db
from fuzzy_helpers import seed_claim
from test_auth import app, client, accounts, claims, bearer, login  # noqa: F401

STAFF_PREDICTION_ROUTES = ("/api/predict/python", "/api/predict/gtm")
REVIEWER_ROUTES = ("/api/review/manual",)


def auth(client, role):
    return bearer(login(client, role)["access_token"])


def evaluate_path(claim_id):
    return f"/api/claims/{claim_id}/evaluate"


# ------------------------------------------------------------------- authentication


@pytest.mark.parametrize(("path", "method", "body"), [
    ("/api/predict/python", "post", {"claim_id": 1}),
    ("/api/predict/gtm", "post", {"claim_id": 1}),
    ("/api/claims/1/evaluate", "post", None),
    ("/api/claims/1/decision", "get", None),
    ("/api/review/manual", "get", None),
    ("/api/review/1/risk", "get", None),
    ("/api/review/1/audit-history", "get", None),
    ("/api/review/1/approve", "post", {"notes": "ok"}),
    ("/api/review/1/reject", "post", {"notes": "ok"}),
    ("/api/review/1/notes", "post", {"notes": "ok"}),
    ("/api/review/1/override", "post", {"decision": "approve", "notes": "ok",
                                         "override_reason": "verified"}),
])
def test_every_new_endpoint_requires_a_token(client, app, path, method, body):
    call = getattr(client, method)
    response = call(path, json=body) if body is not None else call(path)
    assert response.status_code == 401
    assert response.json["error"]["code"] == "unauthorized"


@pytest.mark.parametrize("header", [
    {"Authorization": "Bearer not-a-jwt"},
    {"Authorization": "Bearer "},
    {"Authorization": "Basic YWRtaW46YWRtaW4="},
    {"Authorization": "bearer eyJhbGciOiJIUzI1NiJ9.e30.invalid"},
    {"Authorization": "Bearer eyJhbGciOiJub25lIn0.eyJzdWIiOiIxIn0."},
])
def test_forged_and_malformed_tokens_are_rejected(client, app, header):
    response = client.post("/api/predict/python", headers=header, json={"claim_id": 1})
    assert response.status_code == 401
    assert response.json["error"]["code"] == "unauthorized"


def test_token_signed_with_the_wrong_key_is_rejected(client, app):
    from flask_jwt_extended import create_access_token
    with app.test_request_context():
        forged = create_access_token(identity="1")
    response = client.post("/api/predict/python",
                           headers={"Authorization": f"Bearer {forged}"}, json={"claim_id": 1})
    assert response.status_code == 401


def test_expired_token_is_rejected(client, app, accounts):
    from datetime import timedelta
    from flask_jwt_extended import create_access_token
    with app.test_request_context():
        token = create_access_token(identity=str(accounts["reviewer"]),
                                    expires_delta=timedelta(seconds=-10))
    response = client.post("/api/predict/python",
                           headers={"Authorization": f"Bearer {token}"}, json={"claim_id": 1})
    assert response.status_code == 401


# --------------------------------------------------------------------- authorization


@pytest.mark.parametrize("role", ["customer", "other"])
def test_customers_cannot_trigger_inference_or_evaluation(client, app, claims, role):
    headers = auth(client, role)
    assert client.post("/api/predict/python", headers=headers,
                       json={"claim_id": claims["customer"]["claim"]}).status_code == 403
    assert client.post("/api/predict/gtm", headers=headers,
                       json={"claim_id": claims["customer"]["claim"]}).status_code == 403
    assert client.post(evaluate_path(claims["customer"]["claim"]),
                       headers=headers).status_code == 403


@pytest.mark.parametrize("role", ["customer", "other", "employee"])
def test_only_reviewers_and_admins_reach_the_review_console(client, app, claims, role):
    headers = auth(client, role)
    for path in ("/api/review/manual", f"/api/review/{claims['customer']['claim']}/risk",
                 f"/api/review/{claims['customer']['claim']}/audit-history"):
        assert client.get(path, headers=headers).status_code == 403
    for path in (f"/api/review/{claims['customer']['claim']}/approve",
                 f"/api/review/{claims['customer']['claim']}/reject",
                 f"/api/review/{claims['customer']['claim']}/notes"):
        assert client.post(path, headers=headers, json={"notes": "looks fine"}).status_code == 403


def test_forbidden_responses_do_not_leak_role_or_ownership_details(client, app, claims):
    response = client.post("/api/predict/python", headers=auth(client, "customer"),
                           json={"claim_id": claims["customer"]["claim"]})
    assert response.status_code == 403
    text = response.get_data(as_text=True)
    assert "customer@example.com" not in text
    assert "CLM-" not in text
    assert response.json["error"]["code"] == "forbidden"


def test_employees_cannot_evaluate_claims_assigned_to_other_employees(app, client, accounts):
    ids = seed_claim(app, user_id=accounts["other"], status="submitted")
    with app.app_context():
        db.session.get(Claim, ids["claim"]).assigned_employee_id = None
        db.session.commit()
    assert client.post(evaluate_path(ids["claim"]), headers=auth(client, "employee")).status_code == 404


def test_a_customer_cannot_read_another_customers_decision(client, app, claims):
    response = client.get(f"/api/claims/{claims['customer']['claim']}/decision",
                          headers=auth(client, "other"))
    assert response.status_code == 404


def test_ownership_breach_is_indistinguishable_from_a_missing_claim(client, app, claims):
    other = client.get(f"/api/claims/{claims['customer']['claim']}/decision",
                       headers=auth(client, "other"))
    missing = client.get("/api/claims/999999/decision", headers=auth(client, "other"))
    assert other.status_code == missing.status_code == 404
    assert other.json["error"] == missing.json["error"]


def test_reviewer_cannot_touch_a_claim_assigned_to_another_reviewer(app, client, claims, accounts):
    with app.app_context():
        claim = db.session.get(Claim, claims["customer"]["claim"])
        claim.assigned_reviewer_id = accounts["admin"]
        claim.status, claim.manual_review_required = "manual_review", True
        db.session.commit()
    reviewer = auth(client, "reviewer")
    assert client.get("/api/review/manual", headers=reviewer).json["total"] == 0
    assert client.get(f"/api/review/{claims['customer']['claim']}/risk",
                      headers=reviewer).status_code == 404
    assert client.get(f"/api/review/{claims['customer']['claim']}/audit-history",
                      headers=reviewer).status_code == 404
    assert client.get("/api/review/manual", headers=auth(client, "admin")).json["total"] == 1


def test_draft_claims_are_hidden_from_every_staff_surface(client, app, accounts):
    ids = seed_claim(app, user_id=accounts["customer"], status="draft")
    for role in ("employee", "reviewer", "admin"):
        headers = auth(client, role)
        assert client.post(evaluate_path(ids["claim"]), headers=headers).status_code == 404
        assert client.post("/api/predict/python", headers=headers,
                           json={"claim_id": ids["claim"]}).status_code == 404
        assert client.get(f"/api/claims/{ids['claim']}/decision", headers=headers).status_code == 404


# ---------------------------------------------------------------- input validation


@pytest.mark.parametrize("body", [
    {},
    {"claim_id": 0},
    {"claim_id": -1},
    {"claim_id": -99999},
    {"claim_id": "1"},
    {"claim_id": 1.5},
    {"claim_id": True},
    {"claim_id": None},
    {"claim_id": 1, "extra": "injected"},
    {"claim_id": [1]},
    {"claim_id": {"id": 1}},
])
def test_prediction_bodies_are_strictly_validated(client, app, claims, body):
    response = client.post("/api/predict/python", headers=auth(client, "employee"), json=body)
    assert response.status_code == 400
    assert response.json["error"]["code"] == "validation_error"
    assert response.json["error"]["details"]


def test_prediction_requires_a_json_content_type(client, app, claims):
    response = client.post("/api/predict/python", headers=auth(client, "employee"),
                           data="claim_id=1")
    assert response.status_code == 415
    assert response.json["error"]["code"] == "unsupported_media_type"


def test_prediction_rejects_a_json_array_body(client, app, accounts):
    response = client.post("/api/predict/python", headers=auth(client, "employee"), json=[1, 2])
    assert response.status_code == 400
    assert response.json["error"]["details"] == {"_schema": ["Expected a JSON object."]}


def test_prediction_ignores_a_body_on_the_url_addressed_evaluate_route(client, app, claims):
    """The evaluate route takes the claim id from the path, so no body is needed."""
    for body in ({}, {"claim_id": "not-an-int"}, {"unexpected": True}):
        response = client.post(evaluate_path(claims["customer"]["claim"]),
                               headers=auth(client, "reviewer"), json=body)
        assert response.status_code == 201, response.json


def test_evaluate_path_parameter_cannot_be_negative_or_fractional(client, app, accounts):
    headers = auth(client, "reviewer")
    for path in ("/api/claims/-1/evaluate", "/api/claims/1.5/evaluate", "/api/claims/abc/evaluate",
                 "/api/claims/1;drop/evaluate"):
        assert client.post(path, headers=headers).status_code in {400, 404}


def test_claim_id_must_be_an_integer_not_a_nested_payload(client, app, claims):
    body = {"claim_id": {"$ne": None}}
    response = client.post("/api/predict/python", headers=auth(client, "employee"), json=body)
    assert response.status_code == 400
    with app.app_context():
        assert db.session.query(Claim).count() == 2      # no NoSQL-style mass lookup happened


@pytest.mark.parametrize("body", [
    {},
    {"notes": ""},
    {"notes": "   "},
    {"notes": None},
    {"notes": 123},
    {"notes": "x" * 10001},
    {"notes": "ok", "override_reason": "no decision"},
])
def test_review_bodies_are_validated(client, app, claims, body):
    claim_id = claims["customer"]["claim"]
    with app.app_context():
        db.session.get(Claim, claim_id).status = "manual_review"
        db.session.get(Claim, claim_id).manual_review_required = True
        db.session.commit()
    reviewer = auth(client, "reviewer")
    for path in (f"/api/review/{claim_id}/approve", f"/api/review/{claim_id}/reject",
                 f"/api/review/{claim_id}/notes"):
        assert client.post(path, headers=reviewer, json=body).status_code == 400
    assert client.post(f"/api/review/{claim_id}/override", headers=reviewer, json=body).status_code == 400


@pytest.mark.parametrize("decision", ["maybe", "", None, 1, ["approve"], "APPROVE", "manual_review_continue"])
def test_override_decision_must_be_approve_or_reject(client, app, claims, decision):
    claim_id = claims["customer"]["claim"]
    with app.app_context():
        db.session.get(Claim, claim_id).status = "manual_review"
        db.session.get(Claim, claim_id).manual_review_required = True
        db.session.commit()
    response = client.post(f"/api/review/{claim_id}/override", headers=auth(client, "reviewer"),
                           json={"decision": decision, "notes": "checked",
                                 "override_reason": "verified with retailer"})
    assert response.status_code == 400
    assert response.json["error"]["code"] == "validation_error"


def test_override_requires_a_meaningful_reason(client, app, claims):
    claim_id = claims["customer"]["claim"]
    with app.app_context():
        db.session.get(Claim, claim_id).status = "manual_review"
        db.session.get(Claim, claim_id).manual_review_required = True
        db.session.commit()
    for reason in ("", "  ", "ab", None, "x" * 10001):
        response = client.post(f"/api/review/{claim_id}/override", headers=auth(client, "reviewer"),
            json={"decision": "approve", "notes": "checked", "override_reason": reason})
        assert response.status_code == 400, response.json


def test_review_actions_reject_unknown_fields(client, app, claims):
    claim_id = claims["customer"]["claim"]
    with app.app_context():
        db.session.get(Claim, claim_id).status = "manual_review"
        db.session.get(Claim, claim_id).manual_review_required = True
        db.session.commit()
    response = client.post(f"/api/review/{claim_id}/approve", headers=auth(client, "reviewer"),
                           json={"notes": "checked", "final_decision": "likely_valid"})
    assert response.status_code == 400
    assert "final_decision" in response.json["error"]["details"]


@pytest.mark.parametrize("params", [
    {"page": 0}, {"page": -1}, {"page": "abc"}, {"per_page": 0}, {"per_page": 101},
    {"per_page": "x"}, {"page": 1.5}, {"unexpected": 1},
])
def test_pagination_parameters_are_validated(client, app, accounts, params):
    response = client.get("/api/review/manual", headers=auth(client, "reviewer"),
                          query_string=params)
    assert response.status_code == 400
    assert response.json["error"]["code"] == "validation_error"


def test_review_console_accepts_valid_pagination(client, app, accounts):
    response = client.get("/api/review/manual", headers=auth(client, "reviewer"),
                          query_string={"page": 1, "per_page": 5})
    assert response.status_code == 200
    assert response.json["page"] == 1 and response.json["per_page"] == 5
    assert response.json["total"] == 0 and response.json["items"] == []


# -------------------------------------------------------------- state guards


def test_review_actions_require_a_claim_awaiting_manual_review(client, app, claims):
    claim_id = claims["customer"]["claim"]
    reviewer = auth(client, "reviewer")
    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        claim.status, claim.manual_review_required = "submitted", False
        db.session.commit()
    for path in (f"/api/review/{claim_id}/approve", f"/api/review/{claim_id}/reject",
                 f"/api/review/{claim_id}/notes", f"/api/review/{claim_id}/override"):
        body = {"notes": "checked"} if "override" not in path else {
            "decision": "approve", "notes": "checked", "override_reason": "verified"}
        assert client.post(path, headers=reviewer, json=body).status_code == 409
    assert client.get(f"/api/review/{claim_id}/risk", headers=reviewer).status_code == 409


def test_review_actions_on_an_unknown_claim_are_404(client, app, accounts):
    reviewer = auth(client, "reviewer")
    assert client.post("/api/review/999999/approve", headers=reviewer,
                       json={"notes": "checked"}).status_code == 404
    assert client.get("/api/review/999999/risk", headers=reviewer).status_code == 404


def test_a_claim_cannot_be_reviewed_twice(client, app, claims):
    claim_id = claims["customer"]["claim"]
    reviewer = auth(client, "reviewer")
    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        claim.status, claim.manual_review_required = "manual_review", True
        db.session.commit()
    first = client.post(f"/api/review/{claim_id}/approve", headers=reviewer,
                        json={"notes": "verified"})
    assert first.status_code == 200
    second = client.post(f"/api/review/{claim_id}/approve", headers=reviewer,
                         json={"notes": "again"})
    assert second.status_code == 409


# -------------------------------------------------------------- data isolation


def test_claim_card_payload_contains_no_claimant_identifiers(app, claims, tmp_path):
    import json
    from backend.services.claim_card import card_payload
    with app.app_context():
        claim = db.session.get(Claim, claims["customer"]["claim"])
        from fuzzy_helpers import policy_for
        payload = card_payload(claim, policy_for(app, claim))
    serialized = json.dumps(payload).lower()
    for secret in ("customer@example.com", "password", "example.com", "bearer"):
        assert secret not in serialized


def test_evaluation_response_never_echoes_credentials_or_tokens(client, app, claims, tmp_path):
    app.config["GTM_PREDICTOR"] = lambda path: [0.85, 0.10, 0.05]
    app.config["MODEL_CARD_PATH"] = str(tmp_path / "cards")
    token = login(client, "reviewer")["access_token"]
    response = client.post(evaluate_path(claims["customer"]["claim"]),
                           headers=bearer(token), json={})
    assert response.status_code == 201
    body = response.get_data(as_text=True)
    assert token not in body
    assert "password" not in body.lower()
    assert "test-only-password" not in body


def test_rule_evidence_does_not_leak_storage_paths(client, app, claims, accounts, tmp_path):
    app.config["GTM_PREDICTOR"] = lambda path: [0.85, 0.10, 0.05]
    app.config["MODEL_CARD_PATH"] = str(tmp_path / "cards")
    from fuzzy_helpers import add_document
    add_document(app, claims["customer"]["claim"], accounts["customer"], "receipt",
                 verified={"invoice_number": {"ocr_value": "INV-1", "confirmed_value": "INV-1",
                                              "was_corrected": False, "ocr_confidence": 0.9}})
    response = client.post(evaluate_path(claims["customer"]["claim"]),
                           headers=auth(client, "reviewer"))
    assert response.status_code == 201
    body = response.get_data(as_text=True)
    assert "storage_path" not in body
    assert ".png" not in body


def test_errors_use_the_structured_envelope_everywhere(client, app, claims):
    reviewer = auth(client, "reviewer")
    for response in (client.post("/api/predict/python", headers=reviewer, json={}),
                     client.post("/api/predict/python", headers=reviewer, json={"claim_id": 999999}),
                     client.get("/api/claims/999999/decision", headers=reviewer),
                     client.get("/api/review/999999/risk", headers=reviewer),
                     client.get("/api/review/manual?page=0", headers=reviewer)):
        assert set(response.json) == {"error"}
        assert set(response.json["error"]) >= {"code", "message"}
        assert response.json["error"]["code"] not in {"", None}


def test_server_errors_are_not_leaked_for_malformed_json(client, app, accounts):
    response = client.post("/api/predict/python", headers=auth(client, "employee"),
                           data="{not json", content_type="application/json")
    assert response.status_code == 400
    assert response.json["error"]["code"] in {"validation_error", "malformed_json", "bad_request"}
    assert "Traceback" not in response.get_data(as_text=True)
    assert "File \"" not in response.get_data(as_text=True)
