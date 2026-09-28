from datetime import date, timedelta
from decimal import Decimal
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from backend.db import Base
from backend.db.models import (AuditLog, Claim, Document, GTMPrediction, ModelVersion,
                               Product, PythonPrediction, Review, RuleResult, User, Warranty)
from backend.db.services import create_claim, record_prediction
from backend.db.session import get_engine, session_factory
from backend.schemas.records import UserRead


@pytest.fixture
def db(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'test.sqlite3'}"
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config("backend/db/alembic.ini")
    command.upgrade(cfg, "head")
    engine = get_engine(url)
    with session_factory(engine)() as session:
        yield session
    engine.dispose()
    get_engine.cache_clear()


def graph(db):
    user = User(email="test@example.invalid", password_hash="fake-for-test", first_name="Test", last_name="User")
    db.add(user)
    db.flush()
    product = Product(user_id=user.id, name="Laptop", category="electronics", brand="Example",
                      model_number="M1", serial_number="S123", purchase_date=date(2026, 1, 1),
                      purchase_price=Decimal("100.00"), retailer="Example")
    db.add(product)
    db.flush()
    warranty = Warranty(product_id=product.id, provider="Example", start_date=date(2026, 1, 1),
                        expiry_date=date(2027, 1, 1), coverage_duration_months=12)
    db.add(warranty)
    db.flush()
    claim = create_claim(db, user_id=user.id, product_id=product.id, warranty_id=warranty.id,
                         fault_date=date(2026, 3, 1), fault_type="power", fault_description="Not powering on")
    return user, product, warranty, claim


def test_tables_and_relationships(db):
    assert set(inspect(db.bind).get_table_names()) == set(Base.metadata.tables) | {"alembic_version"}
    user, product, warranty, claim = graph(db)
    db.add_all([
        Document(claim_id=claim.id, uploaded_by=user.id, document_type="receipt", original_filename="a.pdf",
                 stored_filename="random.pdf", mime_type="application/pdf", file_size=1,
                 storage_path="private/random.pdf", file_hash="a" * 64),
        RuleResult(claim_id=claim.id, rule_name="Date", rule_code="date", rule_category="warranty_expiry",
                   result="passed", severity="info", policy_version="v1"),
        Review(claim_id=claim.id, reviewer_user_id=user.id, decision="manual_review_continue"),
    ])
    db.flush()
    assert user.products == [product]
    assert product.warranties == [warranty]
    assert warranty.claims == [claim]
    assert len(claim.documents) == len(claim.rule_results) == len(claim.reviews) == 1
    assert claim.claim_id.startswith("CLM-")
    assert len({user.user_id, product.product_id, warranty.warranty_id, claim.claim_id}) == 4
    assert "password_hash" not in UserRead.model_fields


def test_unique_email_claim_and_fk(db):
    user, product, warranty, claim = graph(db)
    db.commit()
    db.add(User(email=user.email, password_hash="test", first_name="B", last_name="B"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    db.add(Claim(claim_id=claim.claim_id, user_id=user.id, product_id=product.id,
                 warranty_id=warranty.id, fault_date=date.today(), fault_type="x", fault_description="x"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    db.add(Claim(user_id=123456, product_id=product.id, warranty_id=warranty.id,
                 fault_date=date.today(), fault_type="x", fault_description="x"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_claim_ownership_and_prediction_history(db):
    user, product, warranty, claim = graph(db)
    other = User(email="other@example.invalid", password_hash="test", first_name="O", last_name="U")
    db.add(other)
    db.flush()
    with pytest.raises(ValueError):
        create_claim(db, user_id=other.id, product_id=product.id, warranty_id=warranty.id,
                     fault_date=date.today(), fault_type="x", fault_description="x")
    v1 = ModelVersion(model_type="python", model_name="classifier", version="1", artifact_path="v1",
                      training_dataset_version="dataset-1")
    v2 = ModelVersion(model_type="python", model_name="classifier", version="2", artifact_path="v2",
                      training_dataset_version="dataset-2")
    gtm = ModelVersion(model_type="gtm", model_name="card", version="1", artifact_path="gtm1",
                       training_dataset_version="dataset-1")
    db.add_all([v1, v2, gtm])
    db.flush()
    kw = dict(claim_id=claim.id, predicted_class="valid", confidence_valid=Decimal("0.8"),
              confidence_invalid=Decimal("0.1"), confidence_manual_review=Decimal("0.1"))
    a = record_prediction(db, model_version_id=v1.id, **kw)
    b = record_prediction(db, model_version_id=v2.id, **kw)
    c = record_prediction(db, model_version_id=gtm.id, claim_summary_card_path="cards/card.png", **kw)
    assert isinstance(a, PythonPrediction) and isinstance(c, GTMPrediction)
    assert a.model_version.version == "1" and b.model_version.version == "2"
    db.commit()
    with pytest.raises(IntegrityError):
        db.delete(claim)
        db.commit()
    db.rollback()
    assert db.scalar(select(PythonPrediction).where(PythonPrediction.id == a.id)) is not None
    a.predicted_class = "invalid"
    with pytest.raises(ValueError, match="append-only"):
        db.commit()
    db.rollback()
    with pytest.raises(ValueError):
        record_prediction(db, model_version_id=v1.id, **{**kw, "confidence_valid": Decimal("1.5")})


def test_constraints_and_audit(db):
    user, product, warranty, claim = graph(db)
    db.add(AuditLog(user_id=user.id, claim_id=claim.id, action="claim.create", entity_type="claim",
                    entity_id=claim.claim_id, new_values={"status": "draft"}))
    db.commit()
    assert db.scalar(select(AuditLog)).new_values == {"status": "draft"}
    db.add(Document(claim_id=claim.id, uploaded_by=user.id, document_type="receipt", original_filename="a",
                    stored_filename="b", mime_type="application/pdf", file_size=-1, storage_path="b", file_hash="b" * 64))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_downgrade_and_reupgrade(db, monkeypatch):
    cfg = Config("backend/db/alembic.ini")
    command.downgrade(cfg, "base")
    assert "claims" not in inspect(db.bind).get_table_names()
    command.upgrade(cfg, "head")
    assert "claims" in inspect(db.bind).get_table_names()
