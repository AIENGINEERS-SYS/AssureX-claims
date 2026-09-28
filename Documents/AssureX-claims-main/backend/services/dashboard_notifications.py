"""Durable, user-scoped dashboard notices and idempotent warranty reminders."""
from datetime import timedelta
from flask import current_app
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from backend.db.models import Notification, Product
from backend.extensions import db
from .warranty_calculations import current_date, select_current_warranty


def notify(user_id, kind, title, message, *, claim_id=None, product_id=None, dedupe_key=None):
    item = Notification(user_id=user_id, claim_id=claim_id, product_id=product_id,
        dedupe_key=dedupe_key, type=kind, title=title, message=message)
    db.session.add(item)
    return item


def serialize(item):
    return {"id": item.id, "type": item.type, "title": item.title, "message": item.message,
        "claim_id": item.claim_id, "product_id": item.product_id, "is_read": item.is_read,
        "created_at": item.created_at.isoformat(),
        "read_at": item.read_at.isoformat() if item.read_at else None,
        "href": f"/claims/{item.claim_id}" if item.claim_id else
                f"/products/{item.product_id}" if item.product_id else None}


def create_warranty_reminders():
    today = current_date()
    products = db.session.scalars(select(Product).where(Product.is_active.is_(True)).
        options(selectinload(Product.warranties))).yield_per(500)
    due = []
    for product in products:
        warranty = select_current_warranty(product.warranties, today)
        if warranty and warranty.start_date <= today <= warranty.expiry_date <= today + timedelta(
                days=current_app.config["WARRANTY_NEAR_EXPIRY_DAYS"]):
            due.append((product, warranty, f"warranty:{product.id}:{warranty.id}:{warranty.expiry_date.isoformat()}"))
    if not due:
        return 0
    existing = set(db.session.scalars(select(Notification.dedupe_key).where(
        Notification.dedupe_key.in_([key for _, _, key in due]))))
    created = 0
    for product, warranty, key in due:
        if key in existing:
            continue
        try:
            with db.session.begin_nested():
                notify(product.user_id, "warranty_expiry", "Warranty expiring soon",
                    f"{product.name} is covered until {warranty.expiry_date.isoformat()}.",
                    product_id=product.id, dedupe_key=key)
                db.session.flush()
            created += 1
        except IntegrityError:
            continue
    db.session.commit()
    return created
