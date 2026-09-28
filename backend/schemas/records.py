"""Typed API-facing input and response shapes; hashes and internal keys stay private."""
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, EmailStr, Field


class Record(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UserCreate(Record):
    email: EmailStr
    password: str = Field(min_length=12)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    phone: str | None = None


class UserUpdate(Record):
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None


class UserRead(Record):
    user_id: str
    email: EmailStr
    first_name: str
    last_name: str
    phone: str | None
    role: str
    is_active: bool
    created_at: datetime


class ProductCreate(Record):
    name: str
    category: str
    brand: str
    model_number: str
    serial_number: str
    purchase_date: date
    purchase_price: Decimal = Field(ge=0)
    retailer: str


class ProductUpdate(Record):
    name: str | None = None
    retailer: str | None = None
    purchase_price: Decimal | None = Field(default=None, ge=0)


class ProductRead(ProductCreate):
    product_id: str
    created_at: datetime


class WarrantyCreate(Record):
    provider: str
    warranty_type: Literal["standard", "extended"] = "standard"
    start_date: date
    expiry_date: date
    coverage_duration_months: int = Field(gt=0)
    coverage_conditions: dict = Field(default_factory=dict)
    exclusions: list = Field(default_factory=list)
    extended_warranty: bool = False
    service_center_requirements: str | None = None


class WarrantyUpdate(Record):
    expiry_date: date | None = None
    exclusions: list | None = None
    coverage_conditions: dict | None = None


class WarrantyRead(WarrantyCreate):
    warranty_id: str
    created_at: datetime


class ClaimCreate(Record):
    product_id: str
    warranty_id: str
    fault_date: date
    fault_type: str
    fault_description: str
    damage_type: str | None = None


class ClaimUpdate(Record):
    fault_description: str | None = None
    damage_type: str | None = None
    status: str | None = None


class ClaimRead(Record):
    claim_id: str
    status: str
    final_decision: str | None
    manual_review_required: bool
    fault_date: date
    fault_type: str
    fault_description: str
    created_at: datetime


class PredictionRead(Record):
    prediction_id: str
    predicted_class: Literal["valid", "invalid", "manual_review"]
    confidence_valid: Decimal
    confidence_invalid: Decimal
    confidence_manual_review: Decimal
    top_confidence: Decimal
    created_at: datetime
