"""JWT-protected Phase 24 notification APIs."""
from __future__ import annotations

from datetime import date, datetime, time, timezone, timedelta

from flask import Blueprint, request
from flask_jwt_extended import current_user
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import BadRequest, NotFound

from backend.db.models import AuditLog, Notification, NotificationPreference, utcnow
from backend.extensions import db, limiter
from backend.security import role_required
from backend.services.notifications import (
    NotificationPriority,
    NotificationType,
    notification_json,
    preference_json,
)

bp = Blueprint("notifications", __name__, url_prefix="/api/notifications")
ALL_ROLES = ("customer", "employee", "reviewer")
PREFERENCE_FIELDS = {
    "warranty_reminders",
    "claim_updates",
    "information_requests",
    "review_notifications",
}


def _page_args():
    try:
        page = int(request.args.get("page", "1"))
        per_page = int(request.args.get("per_page", "20"))
    except ValueError as exc:
        raise BadRequest("Pagination values must be whole numbers.") from exc
    if page < 1 or not 1 <= per_page <= 100:
        raise BadRequest("Use page >= 1 and per_page between 1 and 100.")
    return page, per_page


def _parse_date_arg(name: str, *, end: bool = False) -> datetime | None:
    raw = request.args.get(name)
    if not raw:
        return None
    try:
        if len(raw) == 10:
            parsed_date = date.fromisoformat(raw)
            return datetime.combine(parsed_date, time.max if end else time.min, tzinfo=timezone.utc)
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise BadRequest(f"{name} must be an ISO-8601 date or datetime.") from exc


def _owned(notification_id: int, *, lock: bool = False) -> Notification:
    query = select(Notification).where(
        Notification.id == notification_id,
        Notification.user_id == current_user.id,
        Notification.deleted_at.is_(None),
    )
    if lock:
        query = query.with_for_update()
    item = db.session.scalar(query)
    if item is None:
        raise NotFound("Notification not found.")
    return item


def _audit(action: str, entity_id: str, *, old=None, new=None, claim_id=None):
    from .common import request_audit_context

    db.session.add(AuditLog(
        user_id=current_user.id,
        claim_id=claim_id,
        action=action,
        entity_type="notifications",
        entity_id=entity_id,
        old_values=old,
        new_values=new,
        **request_audit_context(),
    ))


@bp.get("")
@role_required(*ALL_ROLES)
@limiter.limit("120 per minute")
def list_notifications():
    page, per_page = _page_args()
    query = select(Notification).where(
        Notification.user_id == current_user.id,
        Notification.deleted_at.is_(None),
    )

    notification_type = request.args.get("type", "").strip().upper()
    if notification_type:
        try:
            NotificationType(notification_type)
        except ValueError as exc:
            raise BadRequest("Unknown notification type.") from exc
        query = query.where(Notification.notification_type == notification_type)

    priority = request.args.get("priority", "").strip().upper()
    if priority:
        try:
            NotificationPriority(priority)
        except ValueError as exc:
            raise BadRequest("Unknown notification priority.") from exc
        query = query.where(Notification.priority == priority)

    read_state = request.args.get("is_read")
    if read_state is not None and read_state != "":
        if read_state.lower() not in {"true", "false"}:
            raise BadRequest("is_read must be true or false.")
        query = query.where(Notification.is_read.is_(read_state.lower() == "true"))

    start = _parse_date_arg("from")
    end = _parse_date_arg("to", end=True)
    if start:
        query = query.where(Notification.created_at >= start)
    if end:
        query = query.where(Notification.created_at <= end)
    if start and end and start > end:
        raise BadRequest("from cannot be later than to.")

    search = request.args.get("search", "").strip()
    if len(search) > 120:
        raise BadRequest("Search must contain at most 120 characters.")
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        query = query.where(or_(
            Notification.title.ilike(pattern, escape="\\"),
            Notification.message.ilike(pattern, escape="\\"),
            Notification.reference_id.ilike(pattern, escape="\\"),
        ))

    result = db.paginate(
        query.order_by(Notification.created_at.desc(), Notification.id.desc()),
        page=page,
        per_page=per_page,
        error_out=False,
    )
    return {
        "items": [notification_json(item) for item in result.items],
        "page": page,
        "per_page": per_page,
        "total": result.total,
        "pages": result.pages,
    }


@bp.get("/unread")
@role_required(*ALL_ROLES)
@limiter.limit("120 per minute")
def unread():
    try:
        limit = int(request.args.get("limit", "5"))
    except ValueError as exc:
        raise BadRequest("limit must be a whole number.") from exc
    if not 1 <= limit <= 50:
        raise BadRequest("limit must be between 1 and 50.")
    base = (
        Notification.user_id == current_user.id,
        Notification.deleted_at.is_(None),
        Notification.is_read.is_(False),
    )
    count = db.session.scalar(select(func.count(Notification.id)).where(*base)) or 0
    items = db.session.scalars(
        select(Notification).where(*base)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(limit)
    ).all()
    return {"count": count, "items": [notification_json(item) for item in items]}


@bp.get("/preferences")
@role_required(*ALL_ROLES)
def get_preferences():
    item = db.session.scalar(
        select(NotificationPreference).where(NotificationPreference.user_id == current_user.id)
    )
    return {"preferences": preference_json(item)}


@bp.patch("/preferences")
@role_required(*ALL_ROLES)
def update_preferences():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not payload:
        raise BadRequest("Provide at least one notification preference.")
    unknown = set(payload) - PREFERENCE_FIELDS
    if unknown:
        raise BadRequest("Unknown notification preference: " + ", ".join(sorted(unknown)) + ".")
    if any(type(value) is not bool for value in payload.values()):
        raise BadRequest("Notification preferences must be true or false.")

    item = db.session.scalar(
        select(NotificationPreference)
        .where(NotificationPreference.user_id == current_user.id)
        .with_for_update()
    )
    if item is None:
        try:
            with db.session.begin_nested():
                item = NotificationPreference(user_id=current_user.id)
                db.session.add(item)
                db.session.flush()
        except IntegrityError:
            # A savepoint preserves the rest of this request if two first-time
            # preference updates race on the unique user_id.
            item = db.session.scalar(
                select(NotificationPreference)
                .where(NotificationPreference.user_id == current_user.id)
                .with_for_update()
            )
            if item is None:
                raise
    old = preference_json(item)
    for key, value in payload.items():
        setattr(item, key, value)
    db.session.flush()
    new = preference_json(item)
    _audit("notification.preferences.update", str(current_user.id), old=old, new=new)
    db.session.commit()
    return {"preferences": new}


@bp.get("/analytics")
@role_required("admin")
@limiter.limit("30 per minute")
def analytics():
    try:
        days = int(request.args.get("days", "30"))
    except ValueError as exc:
        raise BadRequest("days must be a whole number.") from exc
    if not 1 <= days <= 3650:
        raise BadRequest("days must be between 1 and 3650.")

    since = utcnow() - timedelta(days=days)
    base = Notification.created_at >= since
    total = db.session.scalar(select(func.count(Notification.id)).where(base)) or 0
    read = db.session.scalar(select(func.count(Notification.id)).where(
        base, Notification.read_at.is_not(None)
    )) or 0

    type_rows = db.session.execute(
        select(Notification.notification_type, func.count(Notification.id))
        .where(base)
        .group_by(Notification.notification_type)
        .order_by(func.count(Notification.id).desc(), Notification.notification_type)
    ).all()
    priority_rows = db.session.execute(
        select(Notification.priority, func.count(Notification.id))
        .where(base)
        .group_by(Notification.priority)
        .order_by(Notification.priority)
    ).all()
    dialect = db.session.get_bind().dialect.name
    if dialect == "postgresql":
        average_expr = func.avg(func.extract(
            "epoch", Notification.read_at - Notification.created_at
        ))
    elif dialect == "sqlite":
        average_expr = func.avg(
            (func.julianday(Notification.read_at) - func.julianday(Notification.created_at)) * 86400.0
        )
    else:
        average_expr = None

    if average_expr is not None:
        average_value = db.session.scalar(
            select(average_expr).where(base, Notification.read_at.is_not(None))
        )
        average_seconds = round(max(0.0, float(average_value)), 2) if average_value is not None else None
    else:
        timings = db.session.execute(
            select(Notification.created_at, Notification.read_at)
            .where(base, Notification.read_at.is_not(None))
        ).all()
        seconds = [
            max(0.0, (read_at - created_at).total_seconds())
            for created_at, read_at in timings
            if created_at is not None and read_at is not None
        ]
        average_seconds = round(sum(seconds) / len(seconds), 2) if seconds else None

    active_unread = (
        base,
        Notification.deleted_at.is_(None),
        Notification.is_read.is_(False),
    )
    critical_unread = db.session.scalar(select(func.count(Notification.id)).where(
        *active_unread, Notification.priority == NotificationPriority.CRITICAL.value
    )) or 0
    high_unread = db.session.scalar(select(func.count(Notification.id)).where(
        *active_unread, Notification.priority.in_(
            (NotificationPriority.HIGH.value, NotificationPriority.CRITICAL.value)
        )
    )) or 0
    stale_high = db.session.scalar(select(func.count(Notification.id)).where(
        *active_unread,
        Notification.priority.in_((NotificationPriority.HIGH.value, NotificationPriority.CRITICAL.value)),
        Notification.created_at < utcnow() - timedelta(hours=24),
    )) or 0

    system_alerts = []
    if critical_unread:
        system_alerts.append({
            "severity": "CRITICAL",
            "title": "Critical notifications awaiting attention",
            "message": f"{critical_unread} critical notification(s) remain unread.",
        })
    if stale_high:
        system_alerts.append({
            "severity": "HIGH",
            "title": "High-priority notifications are aging",
            "message": f"{stale_high} high-priority notification(s) have been unread for more than 24 hours.",
        })

    return {
        "period_days": days,
        "notifications_sent": total,
        "notifications_read": read,
        "read_rate": round(read * 100 / total, 2) if total else 0.0,
        "average_time_to_read_seconds": average_seconds,
        "most_common_notification_type": type_rows[0][0] if type_rows else None,
        "by_type": {kind: count for kind, count in type_rows},
        "by_priority": {priority: count for priority, count in priority_rows},
        "unread_high_priority": high_unread,
        "unread_critical": critical_unread,
        "stale_high_priority": stale_high,
        "system_alerts": system_alerts,
    }


@bp.get("/<int:notification_id>")
@role_required(*ALL_ROLES)
def get_notification(notification_id):
    return {"notification": notification_json(_owned(notification_id))}


@bp.patch("/<int:notification_id>/read")
@role_required(*ALL_ROLES)
def mark_read(notification_id):
    item = _owned(notification_id, lock=True)
    if not item.is_read:
        old = {"is_read": False, "read_at": None}
        item.is_read = True
        item.read_at = utcnow()
        _audit(
            "notification.read",
            item.notification_id,
            old=old,
            new={"is_read": True, "read_at": item.read_at.isoformat()},
            claim_id=item.claim_id,
        )
        db.session.commit()
    return {"notification": notification_json(item)}


@bp.patch("/read-all")
@role_required(*ALL_ROLES)
def mark_all_read():
    now = utcnow()
    result = db.session.execute(
        update(Notification)
        .where(
            Notification.user_id == current_user.id,
            Notification.deleted_at.is_(None),
            Notification.is_read.is_(False),
        )
        .values(is_read=True, read_at=now),
        execution_options={"synchronize_session": False},
    )
    _audit(
        "notification.read_all",
        str(current_user.id),
        new={"count": result.rowcount, "read_at": now.isoformat()},
    )
    db.session.commit()
    return {"updated": result.rowcount}


@bp.delete("/<int:notification_id>")
@role_required(*ALL_ROLES)
def delete_notification(notification_id):
    item = _owned(notification_id, lock=True)
    item.deleted_at = utcnow()
    _audit(
        "notification.delete",
        item.notification_id,
        old={"deleted_at": None},
        new={"deleted_at": item.deleted_at.isoformat()},
        claim_id=item.claim_id,
    )
    db.session.commit()
    return {"deleted": True}
