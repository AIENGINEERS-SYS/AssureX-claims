"""Product HTTP contracts use existing Phase 2 field names."""
from decimal import Decimal, InvalidOperation
from flask import g
from marshmallow import ValidationError, fields, pre_load, validates_schema, validate
from backend.services.warranty_calculations import STATUSES
from .schemas import StrictSchema, PaginationSchema, nonblank


def text_field(maximum, **kwargs):
    return fields.String(validate=[validate.Length(min=1, max=maximum), nonblank], **kwargs)


class Money(fields.Decimal):
    def _deserialize(self, value, attr, data, **kwargs):
        try:
            amount = Decimal(str(value))
            if not amount.is_finite() or amount < 0 or amount > Decimal("9999999999.99") or amount != amount.quantize(Decimal("0.01")):
                raise ValueError()
        except (InvalidOperation, ValueError):
            raise ValidationError("Enter a nonnegative price with at most two decimal places (maximum 9999999999.99).") from None
        return amount.quantize(Decimal("0.01"))


class ProductFields(StrictSchema):
    name = text_field(200, required=True)
    brand = text_field(100, required=True)
    category = text_field(100, required=True)
    model_number = text_field(100, required=True)
    serial_number = text_field(150, required=True)
    purchase_date = fields.Date(required=True)
    purchase_price = Money(required=True)
    retailer = text_field(200, required=True)

    @pre_load
    def strip_strings(self, data, **kwargs):
        if not isinstance(data, dict):
            raise ValidationError("Expected a JSON object.")
        return {key: value.strip() if isinstance(value, str) else value for key, value in data.items()}

    @validates_schema
    def dates(self, data, **kwargs):
        if data.get("purchase_date", g.product_today) > g.product_today:
            raise ValidationError({"purchase_date": ["Purchase date cannot be in the future."]})


class WarrantyInput(StrictSchema):
    provider = text_field(200, required=True)
    start_date = fields.Date(required=True)
    duration = fields.Integer(strict=True, required=True, validate=validate.Range(min=1, max=1200))
    duration_unit = fields.String(required=True, validate=validate.OneOf(["months", "years"]))
    coverage = fields.String(load_default="", validate=validate.Length(max=10000))
    exclusions = fields.List(text_field(1000), load_default=list, validate=validate.Length(max=50))
    service_center_conditions = fields.String(load_default="", validate=validate.Length(max=10000))

    @validates_schema
    def maximum_duration(self, data, **kwargs):
        if data["duration"] * (12 if data["duration_unit"] == "years" else 1) > 1200:
            raise ValidationError({"duration": ["Warranty duration cannot exceed 100 years."]})


class ProductRegistration(ProductFields):
    warranty_duration = fields.Integer(strict=True, required=True, validate=validate.Range(min=1, max=1200))
    warranty_duration_unit = fields.String(required=True, validate=validate.OneOf(["months", "years"]))
    warranty_provider = text_field(200)
    warranty_start_date = fields.Date()
    coverage = fields.String(load_default="", validate=validate.Length(max=10000))
    exclusions = fields.List(text_field(1000), load_default=list, validate=validate.Length(max=50))
    service_center_conditions = fields.String(load_default="", validate=validate.Length(max=10000))


class ProductQuery(PaginationSchema):
    q = fields.String(load_default="", validate=validate.Length(max=200))
    category = fields.String(validate=validate.Length(max=100))
    warranty_status = fields.String(validate=validate.OneOf(STATUSES))
    sort = fields.String(load_default="newest", validate=validate.OneOf([
        "newest", "oldest", "name", "purchase_date", "expiry_date"]))


class WarrantyPreview(StrictSchema):
    start_date = fields.Date(required=True)
    duration = fields.Integer(strict=True, required=True, validate=validate.Range(min=1, max=1200))
    duration_unit = fields.String(required=True, validate=validate.OneOf(["months", "years"]))
