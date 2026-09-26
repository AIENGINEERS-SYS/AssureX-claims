"""Allowlisted Marshmallow inputs; unknown fields are always rejected."""
from flask import request
from marshmallow import Schema, ValidationError, fields, pre_load, validate

ROLES = ("customer", "employee", "reviewer", "admin")


def password_policy(value):
    if len(value) < 12 or len(value.encode("utf-8")) > 72:
        raise ValidationError("Use at least 12 characters and at most 72 UTF-8 bytes.")


def nonblank(value):
    if not value.strip():
        raise ValidationError("Must not be blank.")


class StrictSchema(Schema):
    @pre_load
    def normalize(self, data, **kwargs):
        if not isinstance(data, dict):
            raise ValidationError("Expected a JSON object.")
        data = dict(data)
        for key in ("email", "full_name"):
            if isinstance(data.get(key), str):
                data[key] = data[key].strip()
        if isinstance(data.get("email"), str):
            data["email"] = data["email"].lower()
        return data


class RegisterSchema(StrictSchema):
    full_name = fields.String(required=True, validate=[validate.Length(min=1, max=201), nonblank])
    email = fields.Email(required=True, validate=validate.Length(max=320))
    password = fields.String(required=True, validate=password_policy, load_only=True)


class LoginSchema(StrictSchema):
    email = fields.Email(required=True, validate=validate.Length(max=320))
    password = fields.String(required=True, validate=validate.Length(min=1, max=1024), load_only=True)


class AdminCreateSchema(RegisterSchema):
    role = fields.String(required=True, validate=validate.OneOf(ROLES))


class ProfileSchema(StrictSchema):
    full_name = fields.String(validate=[validate.Length(min=1, max=201), nonblank])
    phone = fields.String(allow_none=True, validate=validate.Length(max=40))


class AdminUpdateSchema(StrictSchema):
    role = fields.String(validate=validate.OneOf(ROLES))
    is_active = fields.Boolean(truthy={True}, falsy={False})


class PasswordSchema(StrictSchema):
    current_password = fields.String(required=True, validate=validate.Length(max=1024), load_only=True)
    new_password = fields.String(required=True, validate=password_policy, load_only=True)


class ClaimSchema(StrictSchema):
    product_id = fields.Integer(required=True, strict=True, validate=validate.Range(min=1))
    warranty_id = fields.Integer(required=True, strict=True, validate=validate.Range(min=1))
    fault_date = fields.Date(required=True)
    fault_type = fields.String(required=True, validate=[validate.Length(min=1, max=100), nonblank])
    fault_description = fields.String(required=True, validate=[validate.Length(min=1, max=10000), nonblank])
    damage_type = fields.String(allow_none=True, validate=validate.Length(max=100))


class StatusSchema(StrictSchema):
    status = fields.String(required=True, validate=validate.OneOf([
        "under_evaluation", "additional_information_required", "manual_review"]))


class AssignmentSchema(StrictSchema):
    employee_id = fields.Integer(required=True, strict=True, validate=validate.Range(min=1))


class ReviewSchema(StrictSchema):
    notes = fields.String(required=True, validate=[validate.Length(min=1, max=10000), nonblank])


class PaginationSchema(Schema):
    page = fields.Integer(load_default=1, validate=validate.Range(min=1, max=100000))
    per_page = fields.Integer(load_default=20, validate=validate.Range(min=1, max=100))


def body(schema):
    return schema.load(request.get_json())


def user_json(user):
    return {key: getattr(user, key) for key in
            ("id", "user_id", "full_name", "email", "phone", "role", "is_active")} | {
        "created_at": user.created_at.isoformat(), "updated_at": user.updated_at.isoformat()}


def claim_json(claim):
    return {key: getattr(claim, key) for key in (
        "id", "claim_id", "user_id", "product_id", "warranty_id", "assigned_employee_id",
        "status", "fault_type", "fault_description", "damage_type", "final_decision",
        "manual_review_required")} | {"fault_date": claim.fault_date.isoformat() if claim.fault_date else None}
