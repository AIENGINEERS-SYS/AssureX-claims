"""Compatibility facade for Phase 23 dashboard notification imports.

Phase 24 moved notification behavior into backend.services.notifications. Keep this
module so older dashboard code and external jobs continue to import safely.
"""
from backend.db.models import Notification
from backend.extensions import db
from .notifications import (
    NotificationPriority,
    NotificationService,
    NotificationType,
    create_warranty_reminders,
    notification_json,
)

serialize = notification_json

_LEGACY_TYPE_MAP = {
    "warranty_expiry": NotificationType.WARRANTY_EXPIRY,
    "document_request": NotificationType.INFO_REQUESTED,
    "reviewer_reminder": NotificationType.INFO_REQUESTED,
    "claim_update": NotificationType.CLAIM_SUBMITTED,
}


def notify(user_id, kind, title, message, *, claim_id=None, product_id=None, dedupe_key=None):
    """Backward-compatible adapter. New code should call NotificationService methods."""
    notification_type = _LEGACY_TYPE_MAP.get(kind)
    if notification_type is None:
        # A legacy/system notice remains persisted and visible without pretending it
        # is one of the seven domain event enum values.
        item = Notification(
            user_id=user_id,
            claim_id=claim_id,
            product_id=product_id,
            dedupe_key=dedupe_key,
            notification_type=str(kind),
            title=title,
            message=message,
            priority=NotificationPriority.MEDIUM.value,
        )
        db.session.add(item)
        return item
    return NotificationService().create_notification(
        user_id=user_id,
        notification_type=notification_type,
        title=title,
        message=message,
        priority=NotificationPriority.MEDIUM,
        reference_type="claim" if claim_id else "product" if product_id else None,
        reference_id=str(claim_id or product_id) if (claim_id or product_id) else None,
        claim_id=claim_id,
        product_id=product_id,
        dedupe_key=dedupe_key,
    )
