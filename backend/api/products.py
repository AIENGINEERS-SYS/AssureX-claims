"""Ownership-scoped product management and ordered, non-overlapping warranties."""
from datetime import timedelta
from flask import Blueprint, current_app, g, request
from flask_jwt_extended import current_user
from marshmallow import ValidationError
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased, selectinload
from werkzeug.exceptions import Conflict, NotFound
from backend.db.models import Product, Warranty
from backend.extensions import db
from backend.security import role_required
from backend.services import warranty_calculations as clock
from backend.services.products import (ensure_unique_serial, product_json, references_product,
    references_warranty, set_warranty, validate_timeline, warranty_json)
from .common import audit
from .product_schemas import ProductFields, ProductQuery, ProductRegistration, WarrantyInput, WarrantyPreview
from .schemas import body

bp = Blueprint("products", __name__, url_prefix="/api")


@bp.before_request
def capture_date():
    g.product_today = clock.current_date()


def owned_product(product_id, *, lock=False):
    query = select(Product).where(Product.id == product_id).options(selectinload(Product.warranties))
    if current_user.role != "admin":
        query = query.where(Product.user_id == current_user.id)
    if lock:
        query = query.with_for_update()
    product = db.session.scalar(query)
    if product is None:
        raise NotFound("Product not found.")
    return product


def flush_product():
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        raise Conflict("A product with this brand, model and serial number is already registered.") from None


@bp.post("/products")
@role_required("customer")
def register():
    data = body(ProductRegistration())
    warranty_data = WarrantyInput().load({
        "provider": data.get("warranty_provider", data["brand"]),
        "start_date": data.get("warranty_start_date", data["purchase_date"]).isoformat(),
        "duration": data["warranty_duration"], "duration_unit": data["warranty_duration_unit"],
        "coverage": data["coverage"], "exclusions": data["exclusions"],
        "service_center_conditions": data["service_center_conditions"]})
    product = Product(user_id=current_user.id, **{key: data[key] for key in ProductFields().fields})
    ensure_unique_serial(product)
    warranty = Warranty(warranty_type="standard", extended_warranty=False)
    set_warranty(warranty, warranty_data)
    validate_timeline(product, warranty)
    product.warranties.append(warranty)
    db.session.add(product)
    flush_product()
    audit("product.create", product, new={"name": product.name, "warranty_id": warranty.id})
    db.session.commit()
    return {"message": "Product registered and warranty calculated.", "product": product_json(product, g.product_today, details=True)}, 201


def query_with_status(today):
    active = and_(Warranty.start_date <= today, Warranty.expiry_date >= today)
    rank = case((and_(active, Warranty.extended_warranty.is_(True)), 4),
                (active, 3), (Warranty.expiry_date < today, 2), else_=1)
    current_id = select(Warranty.id).where(Warranty.product_id == Product.id).order_by(
        rank.desc(), case((Warranty.start_date <= today, Warranty.expiry_date)).desc().nullslast(),
        case((Warranty.start_date > today, Warranty.start_date)).asc().nullslast(),
        case((Warranty.start_date > today, Warranty.id)).asc().nullslast(), Warranty.id.desc()
    ).limit(1).correlate(Product).scalar_subquery()
    selected = aliased(Warranty)
    status = case((selected.id.is_(None), "No Warranty"), (selected.start_date > today, "Not Started"),
        (selected.expiry_date < today, "Expired"), (selected.extended_warranty.is_(True), "Extended Warranty"),
        (selected.expiry_date <= today + timedelta(days=current_app.config["WARRANTY_NEAR_EXPIRY_DAYS"]), "Near Expiry"),
        else_="Active")
    return select(Product).outerjoin(selected, selected.id == current_id), status, selected


@bp.get("/products")
@role_required("customer")
def index():
    args = ProductQuery().load(request.args.to_dict())
    query, status, selected = query_with_status(g.product_today)
    scope = [] if current_user.role == "admin" else [Product.user_id == current_user.id]
    query = query.where(*scope)
    if args["q"].strip():
        query = query.where(or_(*[column.icontains(args["q"].strip(), autoescape=True)
            for column in (Product.name, Product.brand, Product.model_number, Product.serial_number)]))
    if args.get("category"):
        query = query.where(Product.category == args["category"])
    if args.get("warranty_status"):
        query = query.where(status == args["warranty_status"])
    sorting = {"newest": Product.created_at.desc(), "oldest": Product.created_at.asc(),
        "name": func.lower(Product.name), "purchase_date": Product.purchase_date.desc(),
        "expiry_date": selected.expiry_date.asc().nullslast()}
    result = db.paginate(query.options(selectinload(Product.warranties)).order_by(sorting[args["sort"]], Product.id.desc()),
                        page=args["page"], per_page=args["per_page"], error_out=False)
    summary_query, summary_status, _ = query_with_status(g.product_today)
    counts = dict(db.session.execute(summary_query.where(*scope).with_only_columns(
        summary_status, func.count(Product.id)).group_by(summary_status)).all())
    return {"items": [product_json(product, g.product_today) for product in result.items],
        "page": result.page, "per_page": result.per_page, "total": result.total,
        "categories": list(db.session.scalars(select(Product.category).where(*scope).distinct().order_by(Product.category))),
        "server_date": g.product_today.isoformat(), "near_expiry_days": current_app.config["WARRANTY_NEAR_EXPIRY_DAYS"],
        "summary": {"total": sum(counts.values()), "by_status": counts}}


@bp.post("/warranties/preview")
@role_required("customer")
def preview():
    data = body(WarrantyPreview())
    if data["duration"] * (12 if data["duration_unit"] == "years" else 1) > 1200:
        raise ValidationError({"duration": ["Warranty duration cannot exceed 100 years."]})
    try:
        expiry = clock.calculate_warranty_expiry(data["start_date"], data["duration"], data["duration_unit"])
    except ValueError as exc:
        raise ValidationError({"duration": [str(exc)]}) from None
    return {"expiry_date": expiry.isoformat(), "server_date": g.product_today.isoformat()}


@bp.get("/products/<int:product_id>")
@role_required("customer")
def details(product_id):
    return {"product": product_json(owned_product(product_id), g.product_today, details=True)}


@bp.put("/products/<int:product_id>")
@role_required("customer")
def update(product_id):
    product = owned_product(product_id, lock=True)
    data = body(ProductFields())
    # Evidence used in claims must retain its meaning. A display name/retailer may be corrected.
    protected = set(data) - {"name", "retailer"}
    if any(getattr(product, key) != data[key] for key in protected) and references_product(product):
        raise Conflict("Purchase and identity fields cannot change after claims, documents or repairs reference the product.")
    if any(w.start_date < data["purchase_date"] for w in product.warranties):
        raise ValidationError({"purchase_date": ["Purchase date cannot be after a warranty start date."]})
    old = {"name": product.name, "serial_number": product.serial_number}
    for key, value in data.items():
        setattr(product, key, value)
    ensure_unique_serial(product)
    flush_product()
    audit("product.update", product, old=old, new={"name": product.name, "serial_number": product.serial_number})
    db.session.commit()
    return {"message": "Product updated.", "product": product_json(product, g.product_today, details=True)}


@bp.delete("/products/<int:product_id>")
@role_required("customer")
def delete(product_id):
    product = owned_product(product_id, lock=True)
    if references_product(product) or any(references_warranty(w) for w in product.warranties):
        raise Conflict("Products referenced by claims, documents or repairs cannot be deleted.")
    audit("product.delete", product, old={"name": product.name})
    for warranty in product.warranties:
        db.session.delete(warranty)
    db.session.flush()
    db.session.delete(product)
    db.session.commit()
    return {"message": "Product and its unreferenced warranties deleted."}


@bp.get("/products/<int:product_id>/warranties")
@role_required("customer")
def warranty_history(product_id):
    product = owned_product(product_id)
    return {"items": [warranty_json(w, g.product_today) for w in sorted(product.warranties, key=lambda w: (w.start_date, w.id))],
            "server_date": g.product_today.isoformat()}


@bp.post("/products/<int:product_id>/warranties")
@role_required("customer")
def add_warranty(product_id):
    product = owned_product(product_id, lock=True)
    data = body(WarrantyInput())
    extended = bool(product.warranties)
    warranty = Warranty(warranty_type="extended" if extended else "standard", extended_warranty=extended)
    set_warranty(warranty, data)
    validate_timeline(product, warranty)
    product.warranties.append(warranty)
    db.session.flush()
    audit("warranty.create", warranty, new={"product_id": product.id, "is_extended": extended})
    db.session.commit()
    return {"message": "Extended warranty added." if extended else "Original warranty added.",
            "warranty": warranty_json(warranty, g.product_today)}, 201


def owned_warranty(warranty_id):
    # Lock the product first, consistently with additions/updates, to serialize timeline writes.
    query = select(Warranty.product_id).join(Product).where(Warranty.id == warranty_id)
    if current_user.role != "admin":
        query = query.where(Product.user_id == current_user.id)
    product_id = db.session.scalar(query)
    if product_id is None:
        raise NotFound("Warranty not found.")
    product = owned_product(product_id, lock=True)
    warranty = next((w for w in product.warranties if w.id == warranty_id), None)
    if warranty is None:
        raise NotFound("Warranty not found.")
    return product, warranty


@bp.put("/warranties/<int:warranty_id>")
@role_required("customer")
def update_warranty(warranty_id):
    product, warranty = owned_warranty(warranty_id)
    data = body(WarrantyInput())
    if references_warranty(warranty):
        raise Conflict("A warranty used by claims or documents cannot be edited.")
    old = {"expiry_date": warranty.expiry_date.isoformat()}
    set_warranty(warranty, data)
    validate_timeline(product, warranty)
    audit("warranty.update", warranty, old=old, new={"expiry_date": warranty.expiry_date.isoformat()})
    db.session.commit()
    return {"message": "Warranty updated.", "warranty": warranty_json(warranty, g.product_today)}


@bp.delete("/warranties/<int:warranty_id>")
@role_required("customer")
def delete_warranty(warranty_id):
    product, warranty = owned_warranty(warranty_id)
    if references_warranty(warranty):
        raise Conflict("A warranty used by claims or documents cannot be deleted.")
    if not warranty.extended_warranty and len(product.warranties) > 1:
        raise Conflict("Remove extensions before deleting the original warranty.")
    audit("warranty.delete", warranty, old={"product_id": product.id, "expiry_date": warranty.expiry_date.isoformat()})
    db.session.delete(warranty)
    db.session.commit()
    return {"message": "Warranty deleted."}
