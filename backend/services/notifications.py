"""Phase 24 notification domain service and in-app delivery channel.

Notification producers call this service from the same transaction as the domain
change. The in-app channel persists immediately; future email/SMS/push channels
can implement NotificationChannel without changing claim workflows.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Iterable, Protocol

from flask import current_app
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from backend.db.ids import public_id
from backend.db.models import AuditLog, Claim, Notification, NotificationPreference, Product, User
from backend.extensions import db
from .warranty_calculations import current_date, select_current_warranty


class NotificationType(StrEnum):
    WARRANTY_EXPIRY = "WARRANTY_EXPIRY"
    MISSING_DOCUMENTS = "MISSING_DOCUMENTS"
    CLAIM_SUBMITTED = "CLAIM_SUBMITTED"
    INFO_REQUESTED = "INFO_REQUESTED"
    CLAIM_IN_REVIEW = "CLAIM_IN_REVIEW"
    CLAIM_APPROVED = "CLAIM_APPROVED"
    CLAIM_REJECTED = "CLAIM_REJECTED"


class NotificationPriority(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


PREFERENCE_FIELD = {
    NotificationType.WARRANTY_EXPIRY: "warranty_reminders",
    NotificationType.MISSING_DOCUMENTS: "information_requests",
    NotificationType.CLAIM_SUBMITTED: "claim_updates",
    NotificationType.INFO_REQUESTED: "information_requests",
    NotificationType.CLAIM_IN_REVIEW: "review_notifications",
    NotificationType.CLAIM_APPROVED: "claim_updates",
    NotificationType.CLAIM_REJECTED: "claim_updates",
}


@dataclass(frozen=True, slots=True)
class NotificationEvent:
    user_id: int
    notification_type: NotificationType
    title: str
    message: str
    priority: NotificationPriority = NotificationPriority.MEDIUM
    reference_type: str | None = None
    reference_id: str | None = None
    claim_id: int | None = None
    product_id: int | None = None
    dedupe_key: str | None = None


class NotificationChannel(Protocol):
    name: str

    def deliver(self, event: NotificationEvent) -> Notification:
        ...


class InAppNotificationChannel:
    """Current delivery channel. Persistence is intentionally transaction-bound."""

    name = "in_app"

    def deliver(self, event: NotificationEvent) -> Notification:
        item = Notification(
            notification_id=public_id("NTF"),
            user_id=event.user_id,
            claim_id=event.claim_id,
            product_id=event.product_id,
            dedupe_key=event.dedupe_key,
            notification_type=event.notification_type.value,
            title=event.title,
            message=event.message,
            reference_type=event.reference_type,
            reference_id=event.reference_id,
            priority=event.priority.value,
        )
        db.session.add(item)
        db.session.add(AuditLog(
            user_id=None,
            claim_id=event.claim_id,
            action="notification.create",
            entity_type="notifications",
            entity_id=item.notification_id,
            old_values=None,
            new_values={
                "notification_type": event.notification_type.value,
                "priority": event.priority.value,
                "recipient_user_id": event.user_id,
                "reference_type": event.reference_type,
                "reference_id": event.reference_id,
                "channel": self.name,
            },
        ))
        return item


class NotificationService:
    """Role-aware event producer and channel dispatcher."""

    def __init__(self, channels: Iterable[NotificationChannel] | None = None):
        self.channels = tuple(channels or (InAppNotificationChannel(),))

    @staticmethod
    def _preference(user_id: int) -> NotificationPreference | None:
        return db.session.scalar(
            select(NotificationPreference).where(NotificationPreference.user_id == user_id)
        )

    @staticmethod
    def _enabled(
        user_id: int,
        notification_type: NotificationType,
        preference: NotificationPreference | None = None,
    ) -> bool:
        preference = preference if preference is not None else NotificationService._preference(user_id)
        return True if preference is None else bool(getattr(preference, PREFERENCE_FIELD[notification_type]))

    @staticmethod
    def _existing(dedupe_key: str | None) -> bool:
        if not dedupe_key:
            return False
        return db.session.scalar(
            select(Notification.id).where(Notification.dedupe_key == dedupe_key)
        ) is not None

    def create_notification(
        self,
        *,
        user_id: int,
        notification_type: NotificationType | str,
        title: str,
        message: str,
        priority: NotificationPriority | str = NotificationPriority.MEDIUM,
        reference_type: str | None = None,
        reference_id: str | None = None,
        claim_id: int | None = None,
        product_id: int | None = None,
        dedupe_key: str | None = None,
        preference: NotificationPreference | None = None,
        skip_existing_check: bool = False,
    ) -> Notification | None:
        notification_type = NotificationType(notification_type)
        priority = NotificationPriority(priority)
        if not title.strip() or not message.strip():
            raise ValueError("Notification title and message are required.")
        if not self._enabled(user_id, notification_type, preference):
            return None
        if not skip_existing_check and self._existing(dedupe_key):
            return None
        event = NotificationEvent(
            user_id=user_id,
            notification_type=notification_type,
            title=title.strip(),
            message=message.strip(),
            priority=priority,
            reference_type=reference_type,
            reference_id=reference_id,
            claim_id=claim_id,
            product_id=product_id,
            dedupe_key=dedupe_key,
        )
        # In-app is the authoritative first channel. Additional channels may be
        # added later and can enqueue outbox work from the same event.
        delivered = None
        for channel in self.channels:
            item = channel.deliver(event)
            if channel.name == "in_app":
                delivered = item
        return delivered

    def send_claim_submitted(self, claim: Claim) -> Notification | None:
        return self.create_notification(
            user_id=claim.user_id,
            notification_type=NotificationType.CLAIM_SUBMITTED,
            title="Claim submitted",
            message=f"Your claim {claim.claim_id} has been successfully submitted.",
            priority=NotificationPriority.MEDIUM,
            reference_type="claim",
            reference_id=claim.claim_id,
            claim_id=claim.id,
            dedupe_key=f"notification:{claim.user_id}:claim:{claim.id}:submitted",
        )

    def send_claim_approved(self, claim: Claim) -> Notification | None:
        return self.create_notification(
            user_id=claim.user_id,
            notification_type=NotificationType.CLAIM_APPROVED,
            title="Claim approved",
            message=f"Your claim {claim.claim_id} has been approved.",
            priority=NotificationPriority.HIGH,
            reference_type="claim",
            reference_id=claim.claim_id,
            claim_id=claim.id,
            dedupe_key=f"notification:{claim.user_id}:claim:{claim.id}:approved",
        )

    def send_claim_rejected(self, claim: Claim, reason: str | None = None) -> Notification | None:
        reason = (reason or "").strip()
        message = f"Your claim {claim.claim_id} has been rejected."
        if reason:
            message += f" Reason: {reason[:2000]}"
        return self.create_notification(
            user_id=claim.user_id,
            notification_type=NotificationType.CLAIM_REJECTED,
            title="Claim rejected",
            message=message,
            priority=NotificationPriority.HIGH,
            reference_type="claim",
            reference_id=claim.claim_id,
            claim_id=claim.id,
            dedupe_key=f"notification:{claim.user_id}:claim:{claim.id}:rejected",
        )

    def send_missing_document_alert(self, claim: Claim, missing: Iterable[str]) -> Notification | None:
        missing = tuple(sorted({str(value).strip() for value in missing if str(value).strip()}))
        if not missing:
            return None
        display = claim.claim_id if claim.claim_id and not claim.claim_id.startswith("DRF-") else "your claim draft"
        labels = ", ".join(value.replace("_", " ") for value in missing)
        state_key = "|".join(missing)
        return self.create_notification(
            user_id=claim.user_id,
            notification_type=NotificationType.MISSING_DOCUMENTS,
            title="Required documents missing",
            message=f"Additional documents are required to continue processing {display}: {labels}.",
            priority=NotificationPriority.HIGH,
            reference_type="claim",
            reference_id=claim.claim_id,
            claim_id=claim.id,
            dedupe_key=f"notification:{claim.user_id}:claim:{claim.id}:missing:{state_key}",
        )

    def send_information_requested(
        self,
        claim: Claim,
        requested: Iterable[str] | None = None,
        *,
        reminder: bool = False,
        requester: str = "A reviewer",
    ) -> Notification | None:
        requested = tuple(dict.fromkeys(str(value).strip() for value in (requested or ()) if str(value).strip()))
        suffix = ""
        if requested:
            suffix = " Requested: " + ", ".join(value.replace("_", " ") for value in requested) + "."
        title = "Information request reminder" if reminder else "Additional information requested"
        requester = requester.strip() or "A reviewer"
        message = f"{requester} has requested additional information for Claim {claim.claim_id}.{suffix}"
        dedupe = None if reminder else f"notification:{claim.user_id}:claim:{claim.id}:info:{'|'.join(requested) or 'general'}"
        return self.create_notification(
            user_id=claim.user_id,
            notification_type=NotificationType.INFO_REQUESTED,
            title=title,
            message=message,
            priority=NotificationPriority.HIGH,
            reference_type="claim",
            reference_id=claim.claim_id,
            claim_id=claim.id,
            dedupe_key=dedupe,
        )

    def send_review_notification(
        self,
        claim: Claim,
        *,
        notify_customer: bool = True,
        reviewer_id: int | None = None,
    ) -> list[Notification]:
        created: list[Notification] = []
        if notify_customer:
            item = self.create_notification(
                user_id=claim.user_id,
                notification_type=NotificationType.CLAIM_IN_REVIEW,
                title="Claim moved to review",
                message=f"Claim {claim.claim_id} has been moved to manual review.",
                priority=NotificationPriority.HIGH,
                reference_type="claim",
                reference_id=claim.claim_id,
                claim_id=claim.id,
                dedupe_key=f"notification:{claim.user_id}:claim:{claim.id}:manual-review",
            )
            if item:
                created.append(item)

        target_reviewer = reviewer_id if reviewer_id is not None else claim.assigned_reviewer_id
        if target_reviewer:
            reviewer = db.session.scalar(select(User).where(
                User.id == target_reviewer,
                User.role == "reviewer",
                User.is_active.is_(True),
            ))
            if reviewer:
                item = self.create_notification(
                    user_id=reviewer.id,
                    notification_type=NotificationType.CLAIM_IN_REVIEW,
                    title="Claim assigned for review",
                    message=f"Claim {claim.claim_id} is ready for your review.",
                    priority=NotificationPriority.HIGH,
                    reference_type="claim",
                    reference_id=claim.claim_id,
                    claim_id=claim.id,
                    dedupe_key=f"notification:{reviewer.id}:claim:{claim.id}:review-assignment",
                )
                if item:
                    created.append(item)
        return created

    def send_review_assignment(self, claim: Claim, reviewer_id: int) -> Notification | None:
        created = self.send_review_notification(
            claim,
            notify_customer=False,
            reviewer_id=reviewer_id,
        )
        return created[0] if created else None

    def send_warranty_expiry_alert(
        self,
        product: Product,
        warranty,
        days_remaining: int,
        *,
        threshold_days: int | None = None,
        preference: NotificationPreference | None = None,
        skip_existing_check: bool = False,
    ) -> Notification | None:
        return self.create_notification(
            user_id=product.user_id,
            notification_type=NotificationType.WARRANTY_EXPIRY,
            title="Warranty expiring soon",
            message=f"Your warranty for {product.name} expires in {days_remaining} days.",
            priority=NotificationPriority.MEDIUM,
            reference_type="product",
            reference_id=product.product_id,
            product_id=product.id,
            dedupe_key=(
                f"notification:{product.user_id}:warranty:{warranty.id}:"
                f"{warranty.expiry_date.isoformat()}:{threshold_days or days_remaining}"
            ),
            preference=preference,
            skip_existing_check=skip_existing_check,
        )


def notification_json(item: Notification) -> dict:
    official = item.notification_type if item.notification_type in {value.value for value in NotificationType} else None
    href = (
        f"/claims/{item.claim_id}" if item.claim_id else
        f"/products/{item.product_id}" if item.product_id else None
    )
    return {
        "id": item.id,
        "notification_id": item.notification_id,
        "notification_type": item.notification_type,
        # Legacy dashboard clients used "type". Keep it during the Phase 24 transition.
        "type": item.notification_type.lower() if official else item.notification_type,
        "title": item.title,
        "message": item.message,
        "reference_type": item.reference_type,
        "reference_id": item.reference_id,
        "priority": item.priority,
        "claim_id": item.claim_id,
        "product_id": item.product_id,
        "is_read": item.is_read,
        "created_at": item.created_at.isoformat(),
        "read_at": item.read_at.isoformat() if item.read_at else None,
        "href": href,
    }


def preference_json(item: NotificationPreference | None) -> dict:
    return {
        "warranty_reminders": True if item is None else item.warranty_reminders,
        "claim_updates": True if item is None else item.claim_updates,
        "information_requests": True if item is None else item.information_requests,
        "review_notifications": True if item is None else item.review_notifications,
    }


def create_warranty_reminders() -> int:
    """Create exactly one reminder at each configured threshold.

    Product warranties and preferences are eager/bulk loaded, and existing dedupe
    keys are fetched in one query before inserts. The database unique constraint
    is the final concurrency guard for overlapping scheduler runs.
    """
    today = current_date()
    thresholds = set(current_app.config["WARRANTY_NOTIFICATION_THRESHOLDS"])
    products = list(db.session.scalars(
        select(Product)
        .where(Product.is_active.is_(True))
        .options(selectinload(Product.warranties))
        .order_by(Product.id)
    ))
    if not products:
        return 0

    user_ids = {product.user_id for product in products}
    preferences = {
        item.user_id: item for item in db.session.scalars(
            select(NotificationPreference).where(NotificationPreference.user_id.in_(user_ids))
        )
    }
    service = NotificationService()
    due = []
    for product in products:
        warranty = select_current_warranty(product.warranties, today)
        if not warranty or warranty.expiry_date < today:
            continue
        days_remaining = (warranty.expiry_date - today).days
        if days_remaining < 0:
            continue
        eligible_thresholds = [value for value in thresholds if days_remaining <= value]
        if not eligible_thresholds:
            continue
        # Use the nearest not-yet-passed threshold. If a daily job was missed,
        # the next run catches up once instead of silently losing that reminder.
        threshold_days = min(eligible_thresholds)
        preference = preferences.get(product.user_id)
        if not service._enabled(product.user_id, NotificationType.WARRANTY_EXPIRY, preference):
            continue
        key = (
            f"notification:{product.user_id}:warranty:{warranty.id}:"
            f"{warranty.expiry_date.isoformat()}:{threshold_days}"
        )
        due.append((product, warranty, days_remaining, threshold_days, preference, key))

    if not due:
        return 0
    existing = set(db.session.scalars(
        select(Notification.dedupe_key).where(
            Notification.dedupe_key.in_([row[5] for row in due])
        )
    ))

    created = 0
    for product, warranty, days_remaining, threshold_days, preference, key in due:
        if key in existing:
            continue
        try:
            with db.session.begin_nested():
                item = service.send_warranty_expiry_alert(
                    product,
                    warranty,
                    days_remaining,
                    threshold_days=threshold_days,
                    preference=preference,
                    skip_existing_check=True,
                )
                if item is None:
                    continue
                db.session.flush()
            created += 1
        except IntegrityError:
            continue

    db.session.commit()
    return created

