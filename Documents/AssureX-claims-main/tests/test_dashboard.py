"""Role dashboards use migrated SQL, real JWTs and authenticated HTTP calls."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from io import BytesIO
from backend.db.models import (Claim, Document, GTMPrediction, ModelVersion, Notification,
    Product, PythonPrediction, User, Warranty)
from backend.extensions import db
from test_auth import app, client, accounts, claims, login, bearer  # noqa: F401
from test_claim_submission import png


def auth(client, role="customer"):
    return bearer(login(client, role)["access_token"])


def get(client, path, role="customer"):
    return client.get(path, headers=auth(client, role))


def test_access_control(client, accounts, claims):
    for route in ("customer", "reviewer", "admin", "trends", "notifications", "analytics"):
        assert client.get(f"/api/dashboard/{route}").status_code == 401
    assert get(client, "/api/dashboard/admin").status_code == 403
    assert get(client, "/api/dashboard/reviewer").status_code == 403
    assert get(client, "/api/dashboard/admin", "reviewer").status_code == 403
    assert get(client, "/api/dashboard/customer", "reviewer").status_code == 403
    assert get(client, "/api/dashboard/admin", "admin").status_code == 200
    assert get(client, f"/api/dashboard/customer?user_id={accounts['customer']}", "admin").status_code == 200
    assert get(client, "/api/dashboard/customer?user_id=99999", "admin").status_code == 400
    assert get(client, "/api/dashboard/customer?user_id=99999").json["claims"]["total"] == 1
    assert client.get("/dashboard/customer").status_code == 200


def test_customer_warranties_trends_and_actions(client, app, accounts, claims):
    with app.app_context():
        warranty = db.session.get(Warranty, claims["customer"]["warranty"])
        warranty.expiry_date = date.today() + timedelta(days=12)
        old = Product(user_id=accounts["customer"], name="Old camera", category="electronics",
            brand="Example", model_number="C2", serial_number="unique-C2",
            purchase_date=date.today() - timedelta(days=500), purchase_price=Decimal("250"), retailer="Shop")
        db.session.add(old);db.session.flush()
        db.session.add(Warranty(product_id=old.id, provider="Shop",
            start_date=date.today() - timedelta(days=500),
            expiry_date=date.today() - timedelta(days=2), coverage_duration_months=12))
        db.session.add(Claim(user_id=accounts["customer"], status="draft"))
        db.session.commit()
    result = get(client, "/api/dashboard/customer")
    assert result.status_code == 200, result.json
    data = result.json
    assert data["products"]["total_products"] == 2
    assert data["products"]["covered"] == 1 and data["products"]["expired"] == 1
    assert data["products"]["expiring"][0]["days_remaining"] == 12
    assert data["claims"]["total"] == 2 and data["pagination"]["total"] == 2
    assert any(action["kind"] == "resume_draft" for action in data["actions"])
    assert len(get(client, "/api/dashboard/trends?window=7d").json["series"]) == 7
    assert len(get(client, "/api/dashboard/trends?window=12m&interval=month").json["series"]) == 12
    assert get(client, "/api/dashboard/trends?window=invalid").status_code == 400


def test_notification_ownership_and_reminders(client, app, accounts, claims):
    with app.app_context():
        warranty = db.session.get(Warranty, claims["customer"]["warranty"])
        warranty.expiry_date = date.today() + timedelta(days=10)
        other = Notification(user_id=accounts["other"], type="system", title="Private", message="Hidden")
        db.session.add(other);db.session.commit();other_id = other.id
        from backend.services.dashboard_notifications import create_warranty_reminders
        assert create_warranty_reminders() == 1
        assert create_warranty_reminders() == 0
    token = auth(client)
    assert client.patch(f"/api/dashboard/notifications/{other_id}/read", headers=token).status_code == 404
    data = client.get("/api/dashboard/notifications?unread=true", headers=token).json
    assert data["total"] == 1 and data["items"][0]["type"] == "warranty_expiry"
    assert client.patch(f"/api/dashboard/notifications/{data['items'][0]['id']}/read", headers=token).status_code == 200
    assert client.get("/api/dashboard/notifications?unread=true", headers=token).json["total"] == 0


def manual_claim(app, claims):
    with app.app_context():
        claim = db.session.get(Claim, claims["customer"]["claim"])
        claim.status, claim.manual_review_required = "manual_review", True
        claim.submission_date, claim.submitted_at = date.today(), datetime.now(timezone.utc)
        db.session.commit()


def test_reviewer_actions_and_assignment_scope(client, app, accounts, claims):
    manual_claim(app, claims)
    reviewer = auth(client, "reviewer")
    dashboard = client.get("/api/dashboard/reviewer", headers=reviewer)
    assert dashboard.status_code == 200, dashboard.json
    assert dashboard.json["summary"]["pending"] == 1
    assert "damage_evidence" in dashboard.json["missing_documents"][0]["missing"]
    claim_id = claims["customer"]["claim"]
    assert client.patch(f"/api/dashboard/reviewer/claims/{claim_id}/assignment", headers=reviewer, json={}).status_code == 200
    with app.app_context():
        second = User(email="second-reviewer@example.com", full_name="Second", first_name="Second",
            last_name="Reviewer", role="reviewer")
        second.set_password("second-reviewer-password")
        db.session.add(second);db.session.commit()
    other = bearer(client.post("/api/auth/login", json={"email": "second-reviewer@example.com",
        "password": "second-reviewer-password"}).json["access_token"])
    assert client.get(f"/api/dashboard/reviewer/claims/{claim_id}", headers=other).status_code == 404
    assert client.post(f"/api/review/{claim_id}/approve", headers=other, json={"notes": "No"}).status_code == 404
    assert client.get(f"/api/claims/{claim_id}/documents", headers=other).status_code == 404
    response = client.post(f"/api/dashboard/reviewer/claims/{claim_id}/request-documents", headers=reviewer,
        json={"document_types": ["receipt", "damage_evidence"]})
    assert response.status_code == 200, response.json
    assert client.post(f"/api/dashboard/reviewer/claims/{claim_id}/remind", headers=reviewer).status_code == 200
    customer = auth(client)
    version = client.get(f"/api/claims/{claim_id}", headers=customer).json["claim"]["version"]
    uploaded = client.post(f"/api/claims/{claim_id}/documents", headers=customer, data={
        "version": str(version), "document_type": "receipt",
        "file": (BytesIO(png()), "requested.png", "image/png")})
    assert uploaded.status_code == 201, uploaded.json
    assert uploaded.json["claim"]["status"] == "ADDITIONAL_INFORMATION_REQUIRED"
    assert client.post(f"/api/dashboard/reviewer/claims/{claim_id}/resume", headers=other).status_code == 404
    assert client.post(f"/api/dashboard/reviewer/claims/{claim_id}/resume", headers=reviewer).status_code == 200


def test_admin_analytics_disagreement_and_duplicates(client, app, accounts, claims):
    manual_claim(app, claims)
    with app.app_context():
        py = ModelVersion(model_type="python", model_name="Risk", version="1", artifact_path="risk.pkl",
            training_dataset_version="v1", is_active=True, metrics_json={"accuracy": .9, "precision": .85,
                "recall": .8, "f1": .82})
        gtm = ModelVersion(model_type="gtm", model_name="Visual", version="1", artifact_path="model.pt",
            training_dataset_version="v1", is_active=True, metrics_json={})
        db.session.add_all([py, gtm]);db.session.flush()
        claim_id = claims["customer"]["claim"]
        db.session.add_all([PythonPrediction(claim_id=claim_id, model_version_id=py.id,
            predicted_class="valid", confidence_valid=.88, confidence_invalid=.1,
            confidence_manual_review=.02, top_confidence=.88),
            GTMPrediction(claim_id=claim_id, model_version_id=gtm.id,
                predicted_class="invalid", confidence_valid=.15, confidence_invalid=.75,
                confidence_manual_review=.1, top_confidence=.75, claim_summary_card_path="private/path")])
        db.session.add_all([Document(claim_id=claim_id, uploaded_by=accounts["customer"],
            document_type="receipt", original_filename="a.pdf", stored_filename="a.pdf",
            mime_type="application/pdf", file_size=10, storage_path="claims/a.pdf", file_hash="a" * 64),
            Document(claim_id=claims["other"]["claim"], uploaded_by=accounts["other"],
            document_type="receipt", original_filename="b.pdf", stored_filename="b.pdf",
            mime_type="application/pdf", file_size=10, storage_path="claims/b.pdf", file_hash="a" * 64)])
        db.session.commit()
    reviewer = auth(client, "reviewer")
    queue = client.get("/api/dashboard/reviewer", headers=reviewer)
    assert queue.status_code == 200, queue.json
    assert queue.json["queue"][0]["risk_score"] == 75
    assert queue.json["disagreements"][0]["claim_id"]
    duplicate = queue.json["duplicates"][0]
    assert client.post("/api/dashboard/reviewer/duplicates/decision", headers=auth(client), json={
        **duplicate, "status": "confirmed"}).status_code == 403
    decided = client.post("/api/dashboard/reviewer/duplicates/decision", headers=reviewer,
        json={**{key: duplicate[key] for key in ("claim_id", "matching_claim_id", "file_hash")}, "status": "confirmed"})
    assert decided.status_code == 200, decided.json
    data = get(client, "/api/dashboard/admin", "admin").json
    assert data["disagreement"]["processed"] == 1 and data["disagreement"]["rate"] == 100
    assert data["model_confidence"]["average"] == .815
    assert data["duplicate_alerts"]["confirmed"] == 1
    assert get(client, "/api/dashboard/analytics/models?per_page=1", "admin").json["pages"] == 2
    assert get(client, "/api/dashboard/analytics", "admin").json["claims"] == data["claims"]
    assert sum(row["submitted"] for row in get(client, "/api/dashboard/trends?window=7d", "admin").json["series"]) == 1


def test_empty_model_metrics(client, claims):
    data = get(client, "/api/dashboard/admin", "admin").json
    assert data["model_confidence"]["average"] is None
    assert data["disagreement"]["processed"] == 0
    assert data["fraud"]["scored_claims"] == 0


def test_product_deletion_preserves_notice(client, app, accounts):
    with app.app_context():
        product = Product(user_id=accounts["customer"], name="Unclaimed device", category="electronics",
            brand="Example", model_number="D3", serial_number="unique-D3", purchase_date=date.today(),
            purchase_price=Decimal("25"), retailer="Shop")
        db.session.add(product);db.session.flush()
        notice = Notification(user_id=accounts["customer"], product_id=product.id,
            type="system", title="Product notice", message="Saved for audit")
        db.session.add(notice);db.session.commit()
        product_id, notice_id = product.id, notice.id
    response = client.delete(f"/api/products/{product_id}", headers=auth(client))
    assert response.status_code == 200, response.json
    with app.app_context():
        assert db.session.get(Notification, notice_id).product_id is None
