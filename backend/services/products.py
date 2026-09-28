"""Transactions and response calculations shared by product and warranty routes."""
from flask import current_app
from marshmallow import ValidationError
from sqlalchemy import select
from werkzeug.exceptions import Conflict
from backend.db.models import Claim, Document, Product, RepairHistory, Warranty
from backend.db.product_identity import serial_identity
from backend.extensions import db
from .warranty_calculations import (calculate_product_age, calculate_warranty_expiry,
    calculate_warranty_remaining, calculate_warranty_status, select_current_warranty)


def ensure_unique_serial(product):
    key = serial_identity(product.brand, product.model_number, product.serial_number)
    with db.session.no_autoflush:
        duplicate = db.session.scalar(select(Product.id).where(Product.serial_key == key, Product.id != (product.id or 0)))
    if duplicate:
        raise Conflict("A product with this brand, model and serial number is already registered.")


def references_product(product):
    return any(db.session.scalar(select(model.id).where(model.product_id == product.id).limit(1))
               for model in (Claim, Document, RepairHistory))


def references_warranty(warranty):
    return any(db.session.scalar(select(model.id).where(model.warranty_id == warranty.id).limit(1))
               for model in (Claim, Document))


def set_warranty(warranty, data):
    try:
        expiry = calculate_warranty_expiry(data["start_date"], data["duration"], data["duration_unit"])
    except ValueError as exc:
        raise ValidationError({"duration": [str(exc)]}) from None
    warranty.provider = data["provider"].strip()
    warranty.start_date, warranty.expiry_date = data["start_date"], expiry
    warranty.duration_unit = data["duration_unit"]
    warranty.coverage_duration_months = data["duration"] * (12 if data["duration_unit"] == "years" else 1)
    # Preserve structured Phase 2 coverage keys when editing the new description field.
    warranty.coverage_conditions = {**(warranty.coverage_conditions or {}), "description": data["coverage"]}
    warranty.exclusions = data["exclusions"]
    warranty.service_center_requirements = data["service_center_conditions"]


def validate_timeline(product, candidate):
    if candidate.start_date < product.purchase_date:
        raise ValidationError({"start_date": ["Warranty cannot start before the purchase date."]})
    others = [w for w in product.warranties if w is not candidate and (not candidate.id or w.id != candidate.id)]
    if candidate.extended_warranty:
        originals = [w for w in others if not w.extended_warranty]
        if not originals:
            raise Conflict("Register an original warranty before adding an extension.")
        if candidate.start_date <= max(w.expiry_date for w in originals):
            raise ValidationError({"start_date": ["An extension must start after the original warranty expires."]})
    elif any(not w.extended_warranty for w in others):
        raise Conflict("This product already has an original warranty.")
    for existing in others:
        if candidate.start_date <= existing.expiry_date and existing.start_date <= candidate.expiry_date:
            raise ValidationError({"start_date": ["Warranty periods cannot overlap; expiry dates are inclusive."]})
        if not candidate.extended_warranty and candidate.expiry_date >= existing.start_date:
            raise ValidationError({"duration": ["The original warranty must expire before its extensions start."]})


def warranty_json(warranty, today):
    duration = warranty.coverage_duration_months
    if warranty.duration_unit == "years":
        duration //= 12
    active = warranty.start_date <= today <= warranty.expiry_date
    total = max(1, (warranty.expiry_date - warranty.start_date).days)
    coverage = warranty.coverage_conditions
    return {
        "id": warranty.id, "warranty_id": warranty.warranty_id, "product_id": warranty.product_id,
        "provider": warranty.provider, "start_date": warranty.start_date.isoformat(),
        "expiry_date": warranty.expiry_date.isoformat(), "duration": duration,
        "duration_unit": warranty.duration_unit, "coverage_duration_months": warranty.coverage_duration_months,
        "coverage": coverage.get("description", "") if isinstance(coverage, dict) else "",
        "coverage_conditions": coverage, "exclusions": warranty.exclusions,
        "is_extended": warranty.extended_warranty, "warranty_type": warranty.warranty_type,
        "service_center_conditions": warranty.service_center_requirements or "",
        "warranty_status": calculate_warranty_status([warranty], today, current_app.config["WARRANTY_NEAR_EXPIRY_DAYS"]),
        "warranty_remaining": calculate_warranty_remaining(warranty.expiry_date, today, start_date=warranty.start_date),
        "days_remaining": max(0, (warranty.expiry_date - today).days) if active else 0,
        "progress_percent": round(max(0, min(100, (today - warranty.start_date).days / total * 100))),
    }


def product_json(product, today, *, details=False):
    records = sorted(product.warranties, key=lambda w: (w.start_date, w.id))
    current = select_current_warranty(records, today)
    original = next((w for w in records if not w.extended_warranty), None)
    result = {key: getattr(product, key) for key in
        ("id", "product_id", "user_id", "name", "brand", "category", "model_number", "serial_number", "retailer", "is_active")}
    result.update({"purchase_date": product.purchase_date.isoformat(), "purchase_price": str(product.purchase_price),
        "product_age": calculate_product_age(product.purchase_date, today),
        "warranty_duration": warranty_json(original, today)["duration"] if original else None,
        "warranty_duration_unit": original.duration_unit if original else None,
        "warranty_status": calculate_warranty_status(records, today, current_app.config["WARRANTY_NEAR_EXPIRY_DAYS"]),
        "warranty_expiry": current.expiry_date.isoformat() if current else None,
        "warranty_remaining": calculate_warranty_remaining(current.expiry_date if current else None, today,
            start_date=current.start_date if current else None),
        "current_warranty": warranty_json(current, today) if current else None,
        "server_date": today.isoformat(), "near_expiry_days": current_app.config["WARRANTY_NEAR_EXPIRY_DAYS"]})
    if details:
        result["warranties"] = [warranty_json(warranty, today) for warranty in records]
        result["timeline"] = sorted([
            {"date": product.purchase_date.isoformat(), "label": "Purchase", "kind": "purchase"},
            {"date": today.isoformat(), "label": "Today", "kind": "today"},
            *[{"date": day.isoformat(), "label": ("Extended warranty" if w.extended_warranty else "Original warranty") + " " + label,
               "kind": "extension" if w.extended_warranty else "warranty", "warranty_id": w.id}
              for w in records for day, label in [(w.start_date, "starts"), (w.expiry_date, "expires")]]
        ], key=lambda event: event["date"])
    return result
