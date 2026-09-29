"""Durable report jobs and the resource manifest used to recheck downloads."""
from datetime import datetime
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base
from .models import utcnow


class ReportJob(Base):
    __tablename__ = "report_jobs"
    __table_args__ = (
        CheckConstraint("status IN ('queued','running','completed','failed','expired')", name="ck_report_status"),
        CheckConstraint("format IN ('csv','excel','pdf')", name="ck_report_format"),
        Index("ix_report_owner_created", "user_id", "created_at"),
        Index("ix_report_queue", "status", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    role: Mapped[str] = mapped_column(String(32))
    auth_version: Mapped[int] = mapped_column(Integer)
    format: Mapped[str] = mapped_column(String(10))
    filters: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(12), default="queued")
    processed: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ReportItem(Base):
    __tablename__ = "report_items"
    job_id: Mapped[str] = mapped_column(ForeignKey("report_jobs.id", ondelete="CASCADE"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(10), primary_key=True)
    resource_id: Mapped[int] = mapped_column(Integer, primary_key=True)
