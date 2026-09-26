"""Phase 2 relational schema. No business decisions live in these models."""
from datetime import date, datetime, timezone
from decimal import Decimal
from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer,
    JSON, Numeric, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base
from .ids import public_id


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def pid(prefix: str):
    return lambda: public_id(prefix)


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class User(Timestamps, Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('customer','service_center_employee','reviewer','administrator')", name="ck_users_role"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), default=pid("USR"), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(40))
    role: Mapped[str] = mapped_column(String(32), default="customer", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    products: Mapped[list["Product"]] = relationship(back_populates="owner", passive_deletes="all")
    claims: Mapped[list["Claim"]] = relationship(back_populates="user", passive_deletes="all")
    notifications: Mapped[list["Notification"]] = relationship(back_populates="user", passive_deletes="all")


class Product(Timestamps, Base):
    __tablename__ = "products"
    __table_args__ = (CheckConstraint("purchase_price >= 0", name="ck_products_price"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[str] = mapped_column(String(32), default=pid("PRD"), unique=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    brand: Mapped[str] = mapped_column(String(100), nullable=False)
    model_number: Mapped[str] = mapped_column(String(100), nullable=False)
    serial_number: Mapped[str] = mapped_column(String(150), index=True, nullable=False)
    purchase_date: Mapped[date] = mapped_column(Date, nullable=False)
    purchase_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    retailer: Mapped[str] = mapped_column(String(200), nullable=False)
    owner: Mapped[User] = relationship(back_populates="products")
    warranties: Mapped[list["Warranty"]] = relationship(back_populates="product", passive_deletes="all")
    claims: Mapped[list["Claim"]] = relationship(back_populates="product", passive_deletes="all")
    repairs: Mapped[list["RepairHistory"]] = relationship(back_populates="product", passive_deletes="all")


class Warranty(Timestamps, Base):
    __tablename__ = "warranties"
    __table_args__ = (
        CheckConstraint("expiry_date >= start_date", name="ck_warranties_dates"),
        CheckConstraint("coverage_duration_months > 0", name="ck_warranties_duration"),
        CheckConstraint("warranty_type IN ('standard','extended')", name="ck_warranties_type"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    warranty_id: Mapped[str] = mapped_column(String(32), default=pid("WAR"), unique=True, nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"), index=True)
    provider: Mapped[str] = mapped_column(String(200), nullable=False)
    warranty_type: Mapped[str] = mapped_column(String(20), default="standard", nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    expiry_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    coverage_duration_months: Mapped[int] = mapped_column(Integer, nullable=False)
    coverage_conditions: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    exclusions: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    extended_warranty: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    service_center_requirements: Mapped[str | None] = mapped_column(Text)
    product: Mapped[Product] = relationship(back_populates="warranties")
    claims: Mapped[list["Claim"]] = relationship(back_populates="warranty", passive_deletes="all")


class Claim(Timestamps, Base):
    __tablename__ = "claims"
    __table_args__ = (
        CheckConstraint("status IN ('draft','submitted','under_evaluation','additional_information_required','manual_review','approved','rejected','closed')", name="ck_claims_status"),
        CheckConstraint("final_decision IS NULL OR final_decision IN ('likely_valid','likely_invalid','manual_review_required')", name="ck_claims_decision"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[str] = mapped_column(String(32), default=pid("CLM"), unique=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"), index=True)
    warranty_id: Mapped[int] = mapped_column(ForeignKey("warranties.id", ondelete="RESTRICT"), index=True)
    fault_date: Mapped[date] = mapped_column(Date, nullable=False)
    fault_type: Mapped[str] = mapped_column(String(100), nullable=False)
    fault_description: Mapped[str] = mapped_column(Text, nullable=False)
    damage_type: Mapped[str | None] = mapped_column(String(100))
    submission_date: Mapped[date | None] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(40), default="draft", index=True, nullable=False)
    final_decision: Mapped[str | None] = mapped_column(String(32))
    manual_review_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user: Mapped[User] = relationship(back_populates="claims")
    product: Mapped[Product] = relationship(back_populates="claims")
    warranty: Mapped[Warranty] = relationship(back_populates="claims")
    documents: Mapped[list["Document"]] = relationship(back_populates="claim", passive_deletes="all")
    repairs: Mapped[list["RepairHistory"]] = relationship(back_populates="claim", passive_deletes="all")
    python_predictions: Mapped[list["PythonPrediction"]] = relationship(back_populates="claim", passive_deletes="all")
    gtm_predictions: Mapped[list["GTMPrediction"]] = relationship(back_populates="claim", passive_deletes="all")
    rule_results: Mapped[list["RuleResult"]] = relationship(back_populates="claim", passive_deletes="all")
    reviews: Mapped[list["Review"]] = relationship(back_populates="claim", passive_deletes="all")


class Document(Timestamps, Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("file_size >= 0", name="ck_documents_size"),
        CheckConstraint("(claim_id IS NOT NULL) OR (product_id IS NOT NULL) OR (warranty_id IS NOT NULL)", name="ck_documents_parent"),
        CheckConstraint("document_type IN ('receipt','invoice','warranty_card','product_image','serial_number_image','fault_evidence','diagnostic_report','repair_report','other')", name="ck_documents_type"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[str] = mapped_column(String(32), default=pid("DOC"), unique=True, nullable=False)
    claim_id: Mapped[int | None] = mapped_column(ForeignKey("claims.id", ondelete="RESTRICT"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"))
    warranty_id: Mapped[int | None] = mapped_column(ForeignKey("warranties.id", ondelete="RESTRICT"))
    document_type: Mapped[str] = mapped_column(String(40), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    ocr_status: Mapped[str] = mapped_column(String(30), default="pending", nullable=False)
    ocr_text: Mapped[str | None] = mapped_column(Text)
    extracted_data: Mapped[dict | None] = mapped_column(JSON)
    verified_data: Mapped[dict | None] = mapped_column(JSON)
    uploaded_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    claim: Mapped[Claim | None] = relationship(back_populates="documents")


class RepairHistory(Timestamps, Base):
    __tablename__ = "repair_history"
    __table_args__ = (CheckConstraint("repair_cost >= 0", name="ck_repairs_cost"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    repair_id: Mapped[str] = mapped_column(String(32), default=pid("RPR"), unique=True, nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"), index=True)
    claim_id: Mapped[int | None] = mapped_column(ForeignKey("claims.id", ondelete="RESTRICT"), index=True)
    repair_date: Mapped[date] = mapped_column(Date, nullable=False)
    service_center_name: Mapped[str] = mapped_column(String(200), nullable=False)
    authorized_service_center: Mapped[bool] = mapped_column(Boolean, nullable=False)
    parts_replaced: Mapped[str | None] = mapped_column(Text)
    repair_outcome: Mapped[str] = mapped_column(String(100), nullable=False)
    repair_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    product: Mapped[Product] = relationship(back_populates="repairs")
    claim: Mapped[Claim | None] = relationship(back_populates="repairs")


class ModelVersion(Base):
    __tablename__ = "model_versions"
    __table_args__ = (
        UniqueConstraint("model_type", "model_name", "version", name="uq_model_release"),
        CheckConstraint("model_type IN ('python','gtm')", name="ck_models_type"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    model_version_id: Mapped[str] = mapped_column(String(32), default=pid("MOD"), unique=True, nullable=False)
    model_type: Mapped[str] = mapped_column(String(10), nullable=False)
    model_name: Mapped[str] = mapped_column(String(150), nullable=False)
    version: Mapped[str] = mapped_column(String(80), nullable=False)
    artifact_path: Mapped[str] = mapped_column(Text, nullable=False)
    training_dataset_version: Mapped[str] = mapped_column(String(100), nullable=False)
    metrics_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    python_predictions: Mapped[list["PythonPrediction"]] = relationship(back_populates="model_version", passive_deletes="all")
    gtm_predictions: Mapped[list["GTMPrediction"]] = relationship(back_populates="model_version", passive_deletes="all")


class PredictionMixin:
    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id", ondelete="RESTRICT"), index=True)
    model_version_id: Mapped[int] = mapped_column(ForeignKey("model_versions.id", ondelete="RESTRICT"), index=True)
    predicted_class: Mapped[str] = mapped_column(String(30), nullable=False)
    confidence_valid: Mapped[Decimal] = mapped_column(Numeric(7, 6), nullable=False)
    confidence_invalid: Mapped[Decimal] = mapped_column(Numeric(7, 6), nullable=False)
    confidence_manual_review: Mapped[Decimal] = mapped_column(Numeric(7, 6), nullable=False)
    top_confidence: Mapped[Decimal] = mapped_column(Numeric(7, 6), nullable=False)
    inference_duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

class PythonPrediction(PredictionMixin, Base):
    __tablename__ = "python_predictions"
    __table_args__ = (
        CheckConstraint("predicted_class IN ('valid','invalid','manual_review')", name="ck_python_class"),
        *[CheckConstraint(f"{c} >= 0 AND {c} <= 1", name=f"ck_python_{c}") for c in ("confidence_valid", "confidence_invalid", "confidence_manual_review", "top_confidence")],
        CheckConstraint("inference_duration_ms IS NULL OR inference_duration_ms >= 0", name="ck_python_duration"),
    )
    prediction_id: Mapped[str] = mapped_column(String(32), default=pid("PYP"), unique=True, nullable=False)
    claim: Mapped[Claim] = relationship(back_populates="python_predictions")
    model_version: Mapped[ModelVersion] = relationship(back_populates="python_predictions")


class GTMPrediction(PredictionMixin, Base):
    __tablename__ = "gtm_predictions"
    __table_args__ = (
        CheckConstraint("predicted_class IN ('valid','invalid','manual_review')", name="ck_gtm_class"),
        *[CheckConstraint(f"{c} >= 0 AND {c} <= 1", name=f"ck_gtm_{c}") for c in ("confidence_valid", "confidence_invalid", "confidence_manual_review", "top_confidence")],
        CheckConstraint("inference_duration_ms IS NULL OR inference_duration_ms >= 0", name="ck_gtm_duration"),
    )
    prediction_id: Mapped[str] = mapped_column(String(32), default=pid("GTP"), unique=True, nullable=False)
    claim_summary_card_path: Mapped[str] = mapped_column(Text, nullable=False)
    claim: Mapped[Claim] = relationship(back_populates="gtm_predictions")
    model_version: Mapped[ModelVersion] = relationship(back_populates="gtm_predictions")


class RuleResult(Base):
    __tablename__ = "rule_results"
    __table_args__ = (CheckConstraint("result IN ('passed','failed','warning','manual_review')", name="ck_rules_result"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    rule_result_id: Mapped[str] = mapped_column(String(32), default=pid("RUL"), unique=True, nullable=False)
    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id", ondelete="RESTRICT"), index=True)
    rule_name: Mapped[str] = mapped_column(String(150), nullable=False)
    rule_code: Mapped[str] = mapped_column(String(80), nullable=False)
    rule_category: Mapped[str] = mapped_column(String(80), nullable=False)
    result: Mapped[str] = mapped_column(String(30), nullable=False)
    severity: Mapped[str] = mapped_column(String(30), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    claim: Mapped[Claim] = relationship(back_populates="rule_results")


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (CheckConstraint("decision IN ('approve','reject','request_information','manual_review_continue')", name="ck_reviews_decision"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    review_id: Mapped[str] = mapped_column(String(32), default=pid("REV"), unique=True, nullable=False)
    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id", ondelete="RESTRICT"), index=True)
    reviewer_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    comments: Mapped[str | None] = mapped_column(Text)
    override_applied: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    previous_decision: Mapped[str | None] = mapped_column(String(32))
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    claim: Mapped[Claim] = relationship(back_populates="reviews")
    reviewer: Mapped[User] = relationship()


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    notification_id: Mapped[str] = mapped_column(String(32), default=pid("NTF"), unique=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    claim_id: Mapped[int | None] = mapped_column(ForeignKey("claims.id", ondelete="RESTRICT"), index=True)
    type: Mapped[str] = mapped_column(String(60), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    user: Mapped[User] = relationship(back_populates="notifications")


Index("ix_notifications_user_unread", Notification.user_id, Notification.is_read)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    audit_id: Mapped[str] = mapped_column(String(32), default=pid("AUD"), unique=True, nullable=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    claim_id: Mapped[int | None] = mapped_column(ForeignKey("claims.id", ondelete="RESTRICT"), index=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(80), nullable=False)
    old_values: Mapped[dict | None] = mapped_column(JSON)
    new_values: Mapped[dict | None] = mapped_column(JSON)
    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True, nullable=False)
