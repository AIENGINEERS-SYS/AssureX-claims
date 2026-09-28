"""Phase 24 notification API, event, scheduler, preference and security tests."""
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import inspect, select

from backend.db.models import Claim, Notification, NotificationPreference, Product, Warranty
from backend.extensions import db
from backend.services.notifications import (
    NotificationPriority,
    NotificationService,
    NotificationType,
    create_warranty_reminders,
)
from test_auth import app, client, accounts, claims, login, bearer  # noqa: F401


def auth(client, role="customer"):
    return bearer(login(client, role)["access_token"])


def test_notification_schema_is_migrated(app):
    with app.app_context():
        inspector = inspect(db.engine)
        columns = {column["name"] for column in inspector.get_columns("notifications")}
        assert {"reference_type", "reference_id", "priority", "deleted_at"} <= columns
        assert "notification_preferences" in inspector.get_table_names()


def test_notification_creation_retrieval_and_ownership(client, app, accounts, claims):
    with app.app_context():
        customer_claim = db.session.get(Claim, claims["customer"]["claim"])
        other_claim = db.session.get(Claim, claims["other"]["claim"])
        service = NotificationService()
        first = service.send_claim_submitted(customer_claim)
        other = service.send_claim_submitted(other_claim)
        db.session.commit()
        first_id, other_id = first.id, other.id

    customer = auth(client)
    listing = client.get("/api/notifications", headers=customer)
    assert listing.status_code == 200, listing.json
    assert listing.json["total"] == 1
    assert listing.json["items"][0]["notification_type"] == "CLAIM_SUBMITTED"
    assert client.get(f"/api/notifications/{first_id}", headers=customer).status_code == 200
    assert client.get(f"/api/notifications/{other_id}", headers=customer).status_code == 404
    assert client.patch(f"/api/notifications/{other_id}/read", headers=customer).status_code == 404
    assert client.delete(f"/api/notifications/{other_id}", headers=customer).status_code == 404


def test_read_state_mark_all_delete_and_unread_count(client, app, accounts):
    with app.app_context():
        service = NotificationService()
        for index in range(3):
            service.create_notification(
                user_id=accounts["customer"],
                notification_type=NotificationType.CLAIM_SUBMITTED,
                title=f"Notice {index}",
                message=f"Message {index}",
                priority=NotificationPriority.MEDIUM,
                reference_type="claim",
                reference_id=f"CLM-2026-{index:06d}",
                dedupe_key=f"test:read:{index}",
            )
        db.session.commit()

    headers = auth(client)
    unread = client.get("/api/notifications/unread?limit=2", headers=headers)
    assert unread.status_code == 200 and unread.json["count"] == 3 and len(unread.json["items"]) == 2
    first_id = unread.json["items"][0]["id"]
    marked = client.patch(f"/api/notifications/{first_id}/read", headers=headers)
    assert marked.status_code == 200 and marked.json["notification"]["is_read"] is True
    assert client.get("/api/notifications/unread", headers=headers).json["count"] == 2
    all_read = client.patch("/api/notifications/read-all", headers=headers)
    assert all_read.status_code == 200 and all_read.json["updated"] == 2
    assert client.get("/api/notifications?is_read=false", headers=headers).json["total"] == 0
    assert client.delete(f"/api/notifications/{first_id}", headers=headers).status_code == 200
    assert client.get(f"/api/notifications/{first_id}", headers=headers).status_code == 404


def test_filter_search_and_pagination(client, app, accounts):
    with app.app_context():
        service = NotificationService()
        rows = [
            (NotificationType.CLAIM_SUBMITTED, NotificationPriority.MEDIUM, "Claim submitted", "CLM-2026-000001"),
            (NotificationType.CLAIM_APPROVED, NotificationPriority.HIGH, "Claim approved", "CLM-2026-000002"),
            (NotificationType.CLAIM_REJECTED, NotificationPriority.HIGH, "Claim rejected", "CLM-2026-000003"),
            (NotificationType.INFO_REQUESTED, NotificationPriority.HIGH, "Need a receipt", "CLM-2026-000004"),
            (NotificationType.WARRANTY_EXPIRY, NotificationPriority.MEDIUM, "Warranty reminder", "PRD-001"),
        ]
        for index, (kind, priority, title, reference) in enumerate(rows):
            service.create_notification(
                user_id=accounts["customer"],
                notification_type=kind,
                title=title,
                message=f"{title} for {reference}",
                priority=priority,
                reference_type="claim" if reference.startswith("CLM") else "product",
                reference_id=reference,
                dedupe_key=f"test:filter:{index}",
            )
        db.session.commit()

    headers = auth(client)
    page = client.get("/api/notifications?page=1&per_page=2", headers=headers).json
    assert page["total"] == 5 and page["pages"] == 3 and len(page["items"]) == 2
    assert client.get("/api/notifications?type=CLAIM_APPROVED", headers=headers).json["total"] == 1
    assert client.get("/api/notifications?priority=HIGH", headers=headers).json["total"] == 3
    search = client.get("/api/notifications?search=CLM-2026-000004", headers=headers)
    assert search.json["total"] == 1 and search.json["items"][0]["notification_type"] == "INFO_REQUESTED"
    assert client.get("/api/notifications?type=BOGUS", headers=headers).status_code == 400
    assert client.get("/api/notifications?is_read=maybe", headers=headers).status_code == 400
    # SQL wildcard characters in user input are literals, not surprise match-all operators.
    assert client.get("/api/notifications?search=%25", headers=headers).json["total"] == 0
    assert client.get("/api/notifications?search=_", headers=headers).json["total"] == 0


def test_preferences_suppress_selected_event_categories(client, app, accounts, claims):
    headers = auth(client)
    initial = client.get("/api/notifications/preferences", headers=headers)
    assert initial.status_code == 200 and all(initial.json["preferences"].values())
    updated = client.patch("/api/notifications/preferences", headers=headers, json={
        "warranty_reminders": False,
        "claim_updates": False,
    })
    assert updated.status_code == 200
    assert updated.json["preferences"]["warranty_reminders"] is False
    assert updated.json["preferences"]["claim_updates"] is False
    assert client.patch("/api/notifications/preferences", headers=headers, json={"unknown": True}).status_code == 400

    with app.app_context():
        claim = db.session.get(Claim, claims["customer"]["claim"])
        product = db.session.get(Product, claims["customer"]["product"])
        warranty = db.session.get(Warranty, claims["customer"]["warranty"])
        service = NotificationService()
        assert service.send_claim_submitted(claim) is None
        assert service.send_warranty_expiry_alert(product, warranty, 30) is None
        service.send_information_requested(claim, ["receipt"])
        db.session.commit()
        assert db.session.scalar(select(NotificationPreference).where(
            NotificationPreference.user_id == accounts["customer"])) is not None

    data = client.get("/api/notifications", headers=headers).json
    assert data["total"] == 1
    assert data["items"][0]["notification_type"] == "INFO_REQUESTED"


def test_warranty_reminder_thresholds_and_deduplication(app, accounts, claims):
    with app.app_context():
        existing = db.session.get(Warranty, claims["customer"]["warranty"])
        existing.expiry_date = date.today() + timedelta(days=90)
        for index, days in enumerate((60, 30, 7, 15), start=1):
            product = Product(
                user_id=accounts["customer"],
                name=f"Device {days}",
                category="electronics",
                brand="Example",
                model_number=f"M{index + 10}",
                serial_number=f"threshold-{days}",
                purchase_date=date.today() - timedelta(days=20),
                purchase_price=Decimal("100.00"),
                retailer="Shop",
            )
            db.session.add(product)
            db.session.flush()
            db.session.add(Warranty(
                product_id=product.id,
                provider="Example",
                start_date=date.today() - timedelta(days=20),
                expiry_date=date.today() + timedelta(days=days),
                coverage_duration_months=12,
            ))
        db.session.commit()
        app.config["WARRANTY_NOTIFICATION_THRESHOLDS"] = (90, 60, 30, 7)
        assert create_warranty_reminders() == 4
        assert create_warranty_reminders() == 0
        reminders = db.session.scalars(select(Notification).where(
            Notification.notification_type == NotificationType.WARRANTY_EXPIRY.value
        )).all()
        assert len(reminders) == 4
        assert all(item.priority == "MEDIUM" for item in reminders)


def test_warranty_scheduler_catches_up_missed_threshold_without_spam(app, accounts, claims):
    with app.app_context():
        warranty = db.session.get(Warranty, claims["customer"]["warranty"])
        warranty.expiry_date = date.today() + timedelta(days=29)
        app.config["WARRANTY_NOTIFICATION_THRESHOLDS"] = (90, 60, 30, 7)
        db.session.commit()

        assert create_warranty_reminders() == 1
        assert create_warranty_reminders() == 0
        item = db.session.scalar(select(Notification).where(
            Notification.notification_type == NotificationType.WARRANTY_EXPIRY.value
        ))
        assert item is not None
        assert item.dedupe_key.endswith(":30")
        assert "29 days" in item.message


def test_claim_event_helpers_and_reviewer_notification(app, accounts, claims):
    with app.app_context():
        claim = db.session.get(Claim, claims["customer"]["claim"])
        claim.claim_id = "CLM-2026-000001"
        claim.status = "manual_review"
        claim.manual_review_required = True
        claim.assigned_reviewer_id = accounts["reviewer"]
        service = NotificationService()
        assert service.send_missing_document_alert(claim, ["receipt", "warranty_card"])
        created = service.send_review_notification(claim)
        assert len(created) == 2
        assert service.send_claim_approved(claim)
        assert service.send_claim_rejected(claim, "Coverage exclusion confirmed.")
        db.session.commit()

        customer_types = set(db.session.scalars(select(Notification.notification_type).where(
            Notification.user_id == accounts["customer"]
        )))
        reviewer_types = set(db.session.scalars(select(Notification.notification_type).where(
            Notification.user_id == accounts["reviewer"]
        )))
        assert {"MISSING_DOCUMENTS", "CLAIM_IN_REVIEW", "CLAIM_APPROVED", "CLAIM_REJECTED"} <= customer_types
        assert reviewer_types == {"CLAIM_IN_REVIEW"}


def test_reviewer_request_and_decision_generate_notifications(client, app, accounts, claims):
    claim_id = claims["customer"]["claim"]
    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        claim.claim_id = "CLM-2026-000009"
        claim.status = "manual_review"
        claim.manual_review_required = True
        db.session.commit()

    reviewer = auth(client, "reviewer")
    requested = client.post(
        f"/api/dashboard/reviewer/claims/{claim_id}/request-documents",
        headers=reviewer,
        json={"document_types": ["receipt"]},
    )
    assert requested.status_code == 200, requested.json
    customer = auth(client)
    notice = client.get("/api/notifications?type=INFO_REQUESTED", headers=customer)
    assert notice.status_code == 200 and notice.json["total"] == 1

    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        claim.status = "manual_review"
        claim.manual_review_required = True
        db.session.commit()
    approved = client.post(
        f"/api/review/{claim_id}/approve",
        headers=reviewer,
        json={"notes": "Evidence satisfies the warranty terms."},
    )
    assert approved.status_code == 200, approved.json
    assert client.get("/api/notifications?type=CLAIM_APPROVED", headers=customer).json["total"] == 1



def test_rejection_notification_exposes_only_explicit_customer_reason(client, app, accounts, claims):
    claim_id = claims["customer"]["claim"]
    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        claim.claim_id = "CLM-2026-000010"
        claim.status = "manual_review"
        claim.manual_review_required = True
        claim.final_decision = None
        db.session.commit()

    reviewer = auth(client, "reviewer")
    response = client.post(
        f"/api/review/{claim_id}/reject",
        headers=reviewer,
        json={
            "notes": "INTERNAL: reviewer-only evidence and operational commentary.",
            "rejection_reason": "The submitted damage is excluded by the warranty terms.",
        },
    )
    assert response.status_code == 200, response.json

    customer = auth(client)
    notices = client.get("/api/notifications?type=CLAIM_REJECTED", headers=customer)
    assert notices.status_code == 200 and notices.json["total"] == 1
    message = notices.json["items"][0]["message"]
    assert "excluded by the warranty terms" in message
    assert "INTERNAL" not in message


def test_admin_notification_analytics(client, app, accounts):
    with app.app_context():
        service = NotificationService()
        for index in range(4):
            item = service.create_notification(
                user_id=accounts["customer"],
                notification_type=NotificationType.CLAIM_SUBMITTED if index < 3 else NotificationType.CLAIM_APPROVED,
                title=f"Analytics {index}",
                message="Notification analytics fixture.",
                priority=NotificationPriority.HIGH if index == 3 else NotificationPriority.MEDIUM,
                dedupe_key=f"test:analytics:{index}",
            )
            if index < 2:
                from backend.db.models import utcnow
                item.is_read = True
                item.read_at = utcnow()
        db.session.commit()

    assert client.get("/api/notifications/analytics").status_code == 401
    assert client.get("/api/notifications/analytics", headers=auth(client)).status_code == 403
    result = client.get("/api/notifications/analytics?days=30", headers=auth(client, "admin"))
    assert result.status_code == 200, result.json
    assert result.json["notifications_sent"] == 4
    assert result.json["notifications_read"] == 2
    assert result.json["read_rate"] == 50.0
    assert result.json["most_common_notification_type"] == "CLAIM_SUBMITTED"


def test_notification_endpoints_require_authentication(client):
    for method, path in (
        ("get", "/api/notifications"),
        ("get", "/api/notifications/unread"),
        ("get", "/api/notifications/1"),
        ("patch", "/api/notifications/1/read"),
        ("patch", "/api/notifications/read-all"),
        ("delete", "/api/notifications/1"),
        ("get", "/api/notifications/preferences"),
        ("patch", "/api/notifications/preferences"),
    ):
        response = getattr(client, method)(path, json={} if method == "patch" else None)
        assert response.status_code == 401
