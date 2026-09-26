"""Separate incomplete draft validation from final submission validation."""
import unicodedata
from marshmallow import ValidationError, fields, pre_load, validate
from backend.services.warranty_calculations import current_date
from .schemas import StrictSchema

FAULT_TYPES = ("Mechanical Failure", "Electrical Failure", "Software Issue", "Manufacturing Defect",
               "Accidental Damage", "Water Damage", "Overheating", "Other")
DAMAGE_CATEGORIES = ("Minor", "Moderate", "Severe", "Total Loss")
DOCUMENT_TYPES = ("receipt", "invoice", "product_image", "serial_number_image", "damage_evidence",
                  "fault_evidence", "warranty_card", "diagnostic_report", "repair_report", "other")
REQUIRED_DOCUMENTS = ("receipt", "product_image", "serial_number_image", "damage_evidence")


def past_date(value):
    if value and value > current_date():
        raise ValidationError("Fault date cannot be in the future.")


class DraftSchema(StrictSchema):
    product_id = fields.Integer(allow_none=True, strict=True, validate=validate.Range(min=1))
    fault_date = fields.Date(allow_none=True, validate=past_date)
    fault_type = fields.String(allow_none=True, validate=validate.OneOf(FAULT_TYPES))
    description = fields.String(allow_none=True, validate=validate.Length(max=2000))
    damage_category = fields.String(allow_none=True, validate=validate.OneOf(DAMAGE_CATEGORIES))
    repair_history = fields.String(allow_none=True, validate=validate.Length(max=1000))
    previous_replacement = fields.Boolean(truthy={True}, falsy={False})
    current_step = fields.Integer(strict=True, validate=validate.Range(min=1, max=4))

    @pre_load
    def clean_text(self, data, **kwargs):
        if not isinstance(data, dict):
            return data
        result = dict(data)
        for key in ("description", "repair_history"):
            value = result.get(key)
            if isinstance(value, str):
                result[key] = "".join(c for c in unicodedata.normalize("NFC", value)
                                      if c in "\n\t" or not unicodedata.category(c).startswith("C")).strip()
        return result


class DraftUpdateSchema(DraftSchema):
    version = fields.Integer(required=True, strict=True, validate=validate.Range(min=1))


class SubmitSchema(StrictSchema):
    draft_id = fields.Integer(required=True, strict=True, validate=validate.Range(min=1))
    version = fields.Integer(required=True, strict=True, validate=validate.Range(min=1))


class UploadSchema(StrictSchema):
    draft_id = fields.Integer(required=True, validate=validate.Range(min=1))
    version = fields.Integer(required=True, validate=validate.Range(min=1))
    document_type = fields.String(required=True, validate=validate.OneOf(DOCUMENT_TYPES))


class ClaimUploadSchema(StrictSchema):
    version = fields.Integer(required=True, validate=validate.Range(min=1))
    document_type = fields.String(required=True, validate=validate.OneOf(DOCUMENT_TYPES))


class DocumentMutationSchema(StrictSchema):
    version = fields.Integer(required=True, strict=True, validate=validate.Range(min=1))


class OCRReviewSchema(DocumentMutationSchema):
    confirm = fields.Boolean(required=True, truthy={True}, falsy={False}, validate=validate.Equal(True))
    purchase_date = fields.Date(allow_none=True, validate=past_date)
    invoice_number = fields.String(allow_none=True, validate=validate.Length(max=100))
    product_name = fields.String(allow_none=True, validate=validate.Length(max=200))
    model_number = fields.String(allow_none=True, validate=validate.Length(max=100))
    serial_number = fields.String(allow_none=True, validate=validate.Length(max=150))
    retailer = fields.String(allow_none=True, validate=validate.Length(max=200))
    purchase_price = fields.Decimal(allow_none=True, as_string=True, places=2,
                                    validate=validate.Range(min=0, max=9999999999.99))
    warranty_duration = fields.Integer(allow_none=True, strict=True, validate=validate.Range(min=1, max=1200))
    warranty_duration_unit = fields.String(allow_none=True, validate=validate.OneOf(("months", "years")))

    @pre_load
    def clean_review(self, data, **kwargs):
        if not isinstance(data, dict):
            return data
        result = dict(data)
        for key in ("invoice_number", "product_name", "model_number", "serial_number", "retailer"):
            if isinstance(result.get(key), str):
                result[key] = "".join(c for c in unicodedata.normalize("NFC", result[key])
                    if c in "\n\t" or not unicodedata.category(c).startswith("C")).strip() or None
        return result
