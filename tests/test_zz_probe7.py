"""Temporary probe 7 - delete before finishing."""
from datetime import date
import pytest
from backend.db.models import Claim
from backend.extensions import db
from test_auth import app, client, accounts, claims, bearer, login  # noqa: F401


def test_probe_role_matrix(client, claims):
    cid = claims["customer"]["claim"]
    for role in ("customer", "employee", "reviewer", "admin", "other"):
        headers = bearer(login(client, role)["access_token"])
        results = {}
        for label, method, path, body in (
            ("predict/python", "post", "/api/predict/python", {"claim_id": cid}),
            ("predict/gtm", "post", "/api/predict/gtm", {"claim_id": cid}),
            ("evaluate", "post", f"/api/claims/{cid}/evaluate", None),
            ("decision", "get", f"/api/claims/{cid}/decision", None),
            ("risk", "get", f"/api/review/{cid}/risk", None),
            ("manual", "get", "/api/review/manual", None),
            ("audit-history", "get", f"/api/review/{cid}/audit-history", None),
        ):
            call = getattr(client, method)
            response = call(path, headers=headers, json=body) if body is not None else call(path, headers=headers)
            results[label] = response.status_code
        print(f"{role:10}", results)

    print("\nunauthenticated:")
    for label, method, path, body in (
        ("predict/python", "post", "/api/predict/python", {"claim_id": cid}),
        ("evaluate", "post", f"/api/claims/{cid}/evaluate", None),
        ("decision", "get", f"/api/claims/{cid}/decision", None),
    ):
        call = getattr(client, method)
        response = call(path, json=body) if body is not None else call(path)
        print(f"  {label}: {response.status_code}")


def test_probe_body_validation(client, claims):
    cid = claims["customer"]["claim"]
    headers = bearer(login(client, "reviewer")["access_token"])
    for body in ({}, {"claim_id": 0}, {"claim_id": -1}, {"claim_id": "1"}, {"claim_id": 1.5},
                 {"claim_id": cid, "extra": 1}, None, []):
        response = client.post("/api/predict/python", headers=headers, json=body)
        print(f"  predict body {body!r}: {response.status_code} {response.json}")
    for body in ({}, {"notes": ""}, {"notes": "ok", "decision": "maybe"}, {"notes": "ok"}):
        response = client.post(f"/api/review/{cid}/notes", headers=headers, json=body)
        print(f"  notes body {body!r}: {response.status_code}")
    for body in ({}, {"claim_id": "x"}):
        response = client.post("/api/claims/%s/evaluate" % cid, headers=headers, json=body)
        print(f"  evaluate body {body!r}: {response.status_code}")


def test_probe_missing_claim_and_draft(client, app, claims, accounts):
    reviewer = bearer(login(client, "reviewer")["access_token"])
    print("\nunknown claim:", client.post("/api/claims/99999/evaluate", headers=reviewer).status_code)
    print("unknown predict:", client.post("/api/predict/python", headers=reviewer,
                                          json={"claim_id": 99999}).status_code)
    with app.app_context():
        draft = Claim(user_id=accounts["customer"], status="draft", fault_description="x")
        db.session.add(draft)
        db.session.commit()
        draft_id = draft.id
        no_product = Claim(user_id=accounts["customer"], status="submitted", fault_description="x")
        db.session.add(no_product)
        db.session.commit()
        orphan_id = no_product.id
    print("draft evaluate:", client.post(f"/api/claims/{draft_id}/evaluate", headers=reviewer).status_code)
    print("no-product evaluate:", client.post(f"/api/claims/{orphan_id}/evaluate", headers=reviewer).status_code)
    print("no-product decision:", client.get(f"/api/claims/{orphan_id}/decision", headers=reviewer).status_code)
    other = bearer(login(client, "other")["access_token"])
    print("other's claim decision:", client.get(f"/api/claims/{cid_of(claims)}/decision", headers=other).status_code)


def cid_of(claims):
    return claims["customer"]["claim"]
