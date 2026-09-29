"""Private saved criteria and retention-limited search telemetry."""
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, CheckConstraint, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base
from .models import utcnow


class SavedSearch(Base):
    __tablename__ = 'saved_searches'
    __table_args__ = (UniqueConstraint('user_id', 'name', name='uq_saved_search_name'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    name: Mapped[str] = mapped_column(String(80))
    scope: Mapped[str] = mapped_column(String(20))
    filters: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SearchEvent(Base):
    __tablename__ = 'search_events'
    __table_args__ = (
        Index('ix_search_events_user_created', 'user_id', 'created_at'),
        Index('ix_search_events_created', 'created_at'),
        CheckConstraint("outcome IN ('success','empty','invalid','error')", name='ck_search_event_outcome'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'))
    scope: Mapped[str] = mapped_column(String(20))
    query: Mapped[str] = mapped_column(String(120), default='')
    filter_names: Mapped[list] = mapped_column(JSON, default=list)
    filters: Mapped[dict] = mapped_column(JSON, default=dict)
    elapsed_ms: Mapped[int] = mapped_column(Integer)
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    outcome: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SearchFilterUse(Base):
    __tablename__ = 'search_filter_uses'
    event_id: Mapped[int] = mapped_column(ForeignKey('search_events.id', ondelete='CASCADE'), primary_key=True)
    name: Mapped[str] = mapped_column(String(40), primary_key=True)


from .models import Claim, Product, Warranty, PythonPrediction, Review
Index('ix_claims_search_owner_created', Claim.user_id, Claim.created_at, Claim.id)
Index('ix_claims_search_created', Claim.created_at, Claim.id)
Index('ix_claims_search_updated', Claim.updated_at, Claim.id)
Index('ix_products_search_owner_created', Product.user_id, Product.created_at, Product.id)
Index('ix_products_search_created', Product.created_at, Product.id)
Index('ix_warranties_search_current', Warranty.product_id, Warranty.start_date, Warranty.expiry_date)
Index('ix_python_search_latest', PythonPrediction.claim_id, PythonPrediction.created_at, PythonPrediction.id)
Index('ix_python_search_confidence', PythonPrediction.top_confidence, PythonPrediction.claim_id)
Index('ix_reviews_search_date', Review.claim_id, Review.reviewed_at)
Index('ix_products_serial_lower', func.lower(Product.serial_number))
Index('ix_products_category_lower', func.lower(Product.category))
