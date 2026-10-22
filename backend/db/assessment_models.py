"""Durable assessment queue and immutable, claim-specific report versions."""
from datetime import datetime
from uuid import uuid4
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base
from .models import utcnow


class AssessmentJob(Base):
    __tablename__ = "assessment_jobs"
    __table_args__ = (
        UniqueConstraint("claim_id", "version", name="uq_assessment_version"),
        CheckConstraint("status IN ('queued','running','retry_wait','completed','failed','superseded')", name="ck_assessment_status"),
        CheckConstraint("report_status IN ('pending','running','available','failed')", name="ck_assessment_report_status"),
        CheckConstraint("attempts >= 0 AND report_attempts >= 0", name="ck_assessment_attempts"),
        Index("ix_assessment_ready", "status", "available_at"),
        Index("uq_assessment_active_claim", "claim_id", unique=True,
            sqlite_where=text("status IN ('queued','running','retry_wait')"),
            postgresql_where=text("status IN ('queued','running','retry_wait')")),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id", ondelete="RESTRICT"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    requested_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    evidence_digest: Mapped[str] = mapped_column(String(64))
    evidence_snapshot: Mapped[dict] = mapped_column(JSON)
    evaluation_id: Mapped[int | None] = mapped_column(ForeignKey("evaluation_results.id", ondelete="RESTRICT"))
    evaluation_snapshot: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    report_status: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    report_attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AssessmentReport(Base):
    __tablename__ = "assessment_reports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    assessment_id: Mapped[str] = mapped_column(ForeignKey("assessment_jobs.id", ondelete="RESTRICT"), unique=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id", ondelete="RESTRICT"), index=True)
    evaluation_id: Mapped[int] = mapped_column(ForeignKey("evaluation_results.id", ondelete="RESTRICT"))
    version: Mapped[int] = mapped_column(Integer)
    evidence_digest: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="available")
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AssessmentAttempt(Base):
    __tablename__ = "assessment_attempts"
    id: Mapped[int] = mapped_column(primary_key=True)
    assessment_id: Mapped[str] = mapped_column(ForeignKey("assessment_jobs.id", ondelete="RESTRICT"), index=True)
    evaluation_id: Mapped[int] = mapped_column(ForeignKey("evaluation_results.id", ondelete="RESTRICT"), unique=True)
    snapshot: Mapped[dict] = mapped_column(JSON)
    duration_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
