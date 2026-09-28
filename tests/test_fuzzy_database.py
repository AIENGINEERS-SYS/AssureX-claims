"""Database integrity constraints and persistence contracts exercised through the
new rule/evaluation/summary-card surfaces.

Complementary to test_database.py: that file owns the base schema graph, FK and
append-only behaviour, while this one owns the enum-valued check constraints, the
unique keys relied on by new features, JSON round-tripping and RESTRICT semantics
for evaluation/prediction/rule/review rows.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.db.models import (Claim, Document, DuplicateInvestigation, EvaluationResult,
                               ModelVersion, Notification, Product, PythonPrediction, Review,
                               RuleResult, User, Warranty)
from backend.extensions import db
from backend.services.predictions import (
    CANONICAL_LABELS, canonical_label, normalized_result,
)
from fuzzy_helpers import add_document, auth, seed_claim
from test_auth import app, client, accounts, claims  # noqa: F401
from test_claim_submission import draft, complete, submit, upload, png  # noqa: F401


def deny(app, *objs):
    """Add violating rows and confirm the database refuses the commit."""
    with app.app_context():
        for obj in objs:
            db.session.add(obj)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def rw(app):
    return app.app_context()


def doc_row(app, claim_id, uploader, **overrides):
    with rw(app):
        claim = db.session.get(Claim, claim_id)
        base = dict(claim_id=claim.id, uploaded_by=uploader, document_type="receipt",
                    original_filename="a.png", stored_filename="a-stored.png",
                    mime_type="image/png", file_size=10, storage_path="p/a.png",
                    file_hash="0" * 64)
        base.update(overrides)
        return Document(**base)


# ------------------------------------------------------------ documents constraints


def test_document_type_must_be_in_the_stored_vocabulary(app, accounts):
    ids = seed_claim(app, user_id=accounts["customer"])
    deny(app, doc_row(app, ids["claim"], accounts["customer"],
                     document_type="damage_evidence"))   # public name never stored


@pytest.mark.parametrize("bad_type", ["fault_evidence ", "Fault_Evidence", "receipts",
                                      "damageevidence", "copyright-infringement", ""])
def test_document_type_rejects_near_miss_values(app, accounts, bad_type):
    ids = seed_claim(app, user_id=accounts["customer"])
    deny(app, doc_row(app, ids["claim"], accounts["customer"], document_type=bad_type))


def test_every_accepted_stored_document_type_is_writable(app, accounts):
    ids = seed_claim(app, user_id=accounts["customer"])
    with rw(app):
        claim = db.session.get(Claim, ids["claim"])
        for doc_type in ("receipt", "invoice", "warranty_card", "product_image",
                         "serial_number_image", "fault_evidence", "diagnostic_report",
                         "repair_report", "other"):
            db.session.add(Document(claim_id=claim.id, uploaded_by=accounts["customer"],
                                    document_type=doc_type, original_filename="a.png",
                                    stored_filename=f"{doc_type}.png", mime_type="image/png",
                                    file_size=1, storage_path=f"p/{doc_type}.png",
                                    file_hash=f"{doc_type}-{ids['claim']:09d}"))
        db.session.commit()
        got = {d.document_type for d in db.session.scalars(select(Document))}
        assert got == {"receipt", "invoice", "warranty_card", "product_image",
                       "serial_number_image", "fault_evidence", "diagnostic_report",
                       "repair_report", "other"}


def test_a_document_must_point_at_claim_product_or_warranty(app, accounts):
    deny(app, Document(uploaded_by=accounts["customer"], document_type="receipt",
                      original_filename="a.png", stored_filename="a.png",
                      mime_type="image/png", file_size=1, storage_path="p/a.png",
                      file_hash="1" * 64))


def test_same_file_hash_cannot_be_uploaded_twice_against_one_claim(app, accounts):
    ids = seed_claim(app, user_id=accounts["customer"])
    with rw(app):
        claim = db.session.get(Claim, ids["claim"])
        first = dict(claim_id=claim.id, uploaded_by=accounts["customer"],
                     document_type="receipt", original_filename="a.png",
                     stored_filename="a.png", mime_type="image/png", file_size=1,
                     storage_path="p/a.png", file_hash="ab" * 32)
        db.session.add(Document(**first))
        db.session.commit()
        db.session.add(Document(**{**first, "stored_filename": "b.png",
                                   "storage_path": "p/b.png"}))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_drawer_upload_unique_hash_is_shared_across_claims_not_enforced(app, accounts):
    """The same file may back documents on two different claims."""
    one = seed_claim(app, user_id=accounts["customer"])
    two = seed_claim(app, user_id=accounts["customer"])
    with rw(app):
        c1, c2 = db.session.get(Claim, one["claim"]), db.session.get(Claim, two["claim"])
        db.session.add_all([
            Document(claim_id=c1.id, uploaded_by=accounts["customer"], document_type="receipt",
                     original_filename="a.png", stored_filename="a.png", mime_type="image/png",
                     file_size=1, storage_path="p/a.png", file_hash="cd" * 32),
            Document(claim_id=c2.id, uploaded_by=accounts["customer"], document_type="receipt",
                     original_filename="a.png", stored_filename="a-2.png", mime_type="image/png",
                     file_size=1, storage_path="p/a-2.png", file_hash="cd" * 32),
        ])
        db.session.commit()
        assert db.session.query(Document).filter_by(file_hash="cd" * 32).count() == 2


@pytest.mark.parametrize(("field", "value"), [
    ("upload_status", "missing"),
    ("ocr_status", "done"),
    ("review_status", "rejected"),
    ("ocr_status", "processed"),
])
def test_document_status_fields_have_a_closed_vocabulary(app, accounts, field, value):
    ids = seed_claim(app, user_id=accounts["customer"])
    deny(app, doc_row(app, ids["claim"], accounts["customer"], **{field: value}))


@pytest.mark.parametrize(("field", "value"), [
    ("ocr_confidence", Decimal("-0.01")),
    ("ocr_confidence", Decimal("1.01")),
    ("processing_duration_ms", -1),
])
def test_document_quality_metrics_must_stay_in_range(app, accounts, field, value):
    ids = seed_claim(app, user_id=accounts["customer"])
    deny(app, doc_row(app, ids["claim"], accounts["customer"], **{field: value}))


# ------------------------------------------------------------- product constraints


def test_product_serial_identity_is_unique(app, accounts):
    one = seed_claim(app, user_id=accounts["customer"], serial="SN-DUP", model="M-DUP")
    with rw(app):
        existing = db.session.get(Product, one["product"])
        db.session.add(Product(user_id=accounts["customer"], name="Device", category="electronics",
                               brand=existing.brand, model_number=existing.model_number,
                               serial_number=existing.serial_number,
                               purchase_date=date.today(), purchase_price=Decimal("100"),
                               retailer="Shop"))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_products_cannot_have_a_negative_price(app, accounts):
    with rw(app):
        db.session.add(Product(user_id=accounts["customer"], name="Device", category="electronics",
                               brand="X", model_number=f"M{accounts['customer']}",
                               serial_number=f"S{accounts['customer']}",
                               purchase_date=date.today(), purchase_price=Decimal("-1"),
                               retailer="Shop"))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


# ------------------------------------------------------------ warranty constraints


@pytest.mark.parametrize("overrides", [
    {"expiry_date": "before"},
    {"coverage_duration_months": 0},
    {"coverage_duration_months": -3},
    {"warranty_type": "lifetime"},
    {"duration_unit": "weeks"},
])
def test_warranty_rows_validate_dates_duration_and_kind(app, accounts, overrides):
    ids = seed_claim(app, user_id=accounts["customer"])
    with rw(app):
        original = db.session.get(Warranty, ids["warranty"])
        db.session.expunge(original)
        kwargs = dict(product_id=original.product_id, provider="Example",
                      start_date=original.start_date, expiry_date=original.expiry_date,
                      coverage_duration_months=original.coverage_duration_months,
                      warranty_type=original.warranty_type, duration_unit=original.duration_unit)
        for field, value in overrides.items():
            kwargs[field] = original.start_date - timedelta(days=1) if value == "before" else value
        db.session.add(Warranty(**kwargs))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_warranty_expiry_may_equal_start(app, accounts):
    with rw(app):
        db.session.add(Product(user_id=accounts["customer"], name="Device", category="electronics",
                               brand="X", model_number=f"M{accounts['customer']}",
                               serial_number=f"S{accounts['customer']}",
                               purchase_date=date(2026, 1, 1),
                               purchase_price=Decimal("100"), retailer="Shop"))
        db.session.flush()
        product = db.session.scalar(select(Product))
        db.session.add(Warranty(product_id=product.id, provider="Example",
                                start_date=date(2026, 1, 1), expiry_date=date(2026, 1, 1),
                                coverage_duration_months=24))
        db.session.commit()
        assert db.session.scalar(select(Warranty)).expiry_date == \
            db.session.scalar(select(Warranty)).start_date


# ----------------------------------------------------------------- user constraints


def test_user_role_is_a_closed_enum(app):
    deny(app, User(email="x@example.com", password_hash="hash", first_name="A", last_name="B",
                  role="superadmin"))


def test_email_is_normalized_on_any_insert(app):
    with rw(app):
        db.session.add(User(email="  Mixed.Case@Example.COM ", password_hash="hash",
                            first_name="A", last_name="B"))
        db.session.commit()
        assert db.session.scalar(select(User).where(User.email == "mixed.case@example.com"))


# ----------------------------------------------------------------- claim constraints


@pytest.mark.parametrize(("field", "value"), [
    ("current_step", 0),
    ("current_step", 5),
    ("status", "deleted"),
    ("status", "pending"),
    ("final_decision", "approved"),
    ("final_decision", "low_risk"),
])
def test_claim_state_machine_columns_are_guarded(app, accounts, field, value):
    ids = seed_claim(app, user_id=accounts["customer"])
    with rw(app):
        claim = db.session.get(Claim, ids["claim"])
        setattr(claim, field, value)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_claim_status_and_decision_accept_every_legal_value(app, accounts):
    with rw(app):
        for status in ("draft", "submitted", "under_evaluation", "additional_information_required",
                       "manual_review", "approved", "rejected", "closed"):
            claim = Claim(user_id=accounts["customer"], status=status,
                          fault_date=date.today(), fault_type="power", fault_description="x")
            db.session.add(claim)
        for decision in ("likely_valid", "likely_invalid", "manual_review_required"):
            claim = Claim(user_id=accounts["customer"], status="approved", final_decision=decision,
                          fault_date=date.today(), fault_type="power", fault_description="x")
            db.session.add(claim)
        db.session.commit()
        assert db.session.query(Claim).count() == 11


# -------------------------------------------------- prediction & rule row contracts


def test_rule_result_vocabulary_is_guarded(app, accounts):
    ids = seed_claim(app, user_id=accounts["customer"])
    deny(app, RuleResult(claim_id=ids["claim"], rule_name="Warranty", rule_code="warranty_expiry",
                        rule_category="warranty_expiry", result="skipped", severity="info",
                        policy_version="v1", details={}))


def test_rule_result_json_is_round_tripped_faithfully(app, accounts):
    ids = seed_claim(app, user_id=accounts["customer"])
    with rw(app):
        db.session.add(RuleResult(claim_id=ids["claim"], rule_name="Serial", rule_code="serial",
                                  rule_category="serial_match", result="failed", severity="high",
                                  policy_version="2026.01.0",
                                  details={"evidence": {"state": "mismatch"},
                                           "values": [1, 2, {"k": "v"}]}))
        db.session.commit()
        row = db.session.scalar(select(RuleResult))
        assert row.details == {"evidence": {"state": "mismatch"}, "values": [1, 2, {"k": "v"}]}
        assert row.claim_id == ids["claim"]
        assert row.rule_result_id in [r.rule_result_id for r in row.claim.rule_results]
        assert row.policy_version == "2026.01.0"


def test_evaluation_payload_columns_are_json_round_tripped(app, accounts):
    ids = seed_claim(app, user_id=accounts["customer"])
    payload = {
        "comparison": {"status": "Strong Match", "distance": 0.05},
        "duplicate_finding": {"duplicate_risk": "low", "matches": []},
        "contradictions": [{"rule_code": "fault_after_claim"}],
        "explanation": {"rules": ["warranty_expiry"], "model_disagreement": False},
        "model_errors": {},
    }
    with rw(app):
        db.session.add(EvaluationResult(claim_id=ids["claim"], status="complete",
                                        recommendation="likely_valid", policy_code="electronics",
                                        policy_version="2026.01.0", **payload))
        db.session.commit()
        row = db.session.scalar(select(EvaluationResult))
        for field, expected in payload.items():
            assert getattr(row, field) == expected
        assert row.policy_code == "electronics"
        assert row.evaluation_id.startswith("EVL-")


@pytest.mark.parametrize(("field", "value"), [
    ("status", "done"),
    ("recommendation", "approved"),
    ("recommendation", "invalid"),
])
def test_evaluation_result_vocabulary_is_guarded(app, accounts, field, value):
    ids = seed_claim(app, user_id=accounts["customer"])
    kwargs = dict(claim_id=ids["claim"], status="complete", recommendation="likely_valid",
                  policy_version="v1")
    kwargs[field] = value
    deny(app, EvaluationResult(**kwargs))


def test_python_prediction_rows_are_class_and_confidence_guarded(app, accounts):
    ids = seed_claim(app, user_id=accounts["customer"])
    with rw(app):
        version = ModelVersion(model_type="python", model_name="classifier", version="1",
                               artifact_path="models/py.pt", training_dataset_version="d1")
        db.session.add(version)
        db.session.flush()
        base = dict(claim_id=ids["claim"], model_version_id=version.id, predicted_class="valid",
                    confidence_valid=Decimal("0.8"), confidence_invalid=Decimal("0.1"),
                    confidence_manual_review=Decimal("0.1"), top_confidence=Decimal("0.8"))
        db.session.add(PythonPrediction(**{**base, "predicted_class": "auto_approve"}))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()
        db.session.add(PythonPrediction(**{**base, "confidence_valid": Decimal("2.0"),
                                           "top_confidence": Decimal("2.0")}))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_evaluation_rows_block_claim_deletion_as_an_audit_trail(app, accounts):
    ids = seed_claim(app, user_id=accounts["customer"])
    with rw(app):
        db.session.add(EvaluationResult(claim_id=ids["claim"], status="complete",
                                        recommendation="likely_valid", policy_version="v1"))
        db.session.commit()
        claim = db.session.get(Claim, ids["claim"])
        db.session.delete(claim)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()
        assert db.session.query(EvaluationResult).count() == 1
        assert db.session.get(Claim, ids["claim"]) is not None


# ------------------------------------------------------- review & duplicate contracts


def test_review_decision_vocabulary_is_guarded(app, accounts):
    ids = seed_claim(app, user_id=accounts["customer"])
    deny(app, Review(claim_id=ids["claim"], reviewer_user_id=accounts["reviewer"],
                    decision="escalate"))


def test_reviewer_must_exist_so_no_dangling_reviews(app, accounts):
    ids = seed_claim(app, user_id=accounts["customer"])
    deny(app, Review(claim_id=ids["claim"], reviewer_user_id=999999, decision="approve",
                    comments="ok"))


@pytest.mark.parametrize("status", ["confirmed", "false_positive"])
def test_duplicate_investigation_accepts_only_legal_statuses(app, accounts, status):
    first = seed_claim(app, user_id=accounts["customer"])
    second = seed_claim(app, user_id=accounts["customer"])
    low, high = sorted((first["claim"], second["claim"]))
    deny(app, DuplicateInvestigation(claim_id=low, matching_claim_id=high,
                                    file_hash="ab" * 32, status="pending",
                                    reviewer_user_id=accounts["reviewer"]))
    with rw(app):
        db.session.add(DuplicateInvestigation(claim_id=low, matching_claim_id=high,
                                              file_hash="ab" * 32, status=status,
                                              reviewer_user_id=accounts["reviewer"]))
        db.session.commit()
        assert db.session.query(DuplicateInvestigation).count() == 1


def test_duplicate_pairs_must_be_ordered_and_unique(app, accounts):
    first = seed_claim(app, user_id=accounts["customer"])
    second = seed_claim(app, user_id=accounts["customer"])
    low, high = sorted((first["claim"], second["claim"]))
    with rw(app):
        db.session.add(DuplicateInvestigation(claim_id=low, matching_claim_id=high,
                                              file_hash="ab" * 32, status="confirmed",
                                              reviewer_user_id=accounts["reviewer"]))
        db.session.commit()
    # reversed order mirrors the pair and is refused
    deny(app, DuplicateInvestigation(claim_id=high, matching_claim_id=low,
                                    file_hash="ab" * 32, status="confirmed",
                                    reviewer_user_id=accounts["reviewer"]))
    # same pair + same hash is a duplicate row
    deny(app, DuplicateInvestigation(claim_id=low, matching_claim_id=high,
                                    file_hash="ab" * 32, status="false_positive",
                                    reviewer_user_id=accounts["reviewer"]))
    # a different hash for the same pair is fine
    with rw(app):
        db.session.add(DuplicateInvestigation(claim_id=low, matching_claim_id=high,
                                              file_hash="cd" * 32, status="confirmed",
                                              reviewer_user_id=accounts["reviewer"]))
        db.session.commit()
        assert db.session.query(DuplicateInvestigation).count() == 2


def test_review_rows_persist_override_and_previous_decision(app, client, accounts):
    ids = seed_claim(app, user_id=accounts["customer"], status="manual_review")
    with rw(app):
        db.session.get(Claim, ids["claim"]).manual_review_required = True
        db.session.commit()
    reviewer = auth(client, "reviewer")
    response = client.post(f"/api/review/{ids['claim']}/override", headers=reviewer,
                           json={"decision": "reject", "notes": "double claim",
                                 "override_reason": "duplicate invoices"})
    assert response.status_code == 200
    with rw(app):
        row = db.session.scalar(select(Review))
        assert row.decision == "reject"
        assert row.override_applied is True
        assert row.override_reason == "duplicate invoices"
        assert row.comments == "double claim"
        assert row.reviewer_user_id == accounts["reviewer"]
        assert row.claim_id == ids["claim"]
        assert row.review_id.startswith("REV-")


def test_notification_dedupe_key_is_unique(app, accounts):
    ids = seed_claim(app, user_id=accounts["customer"])
    with rw(app):
        db.session.add(Notification(user_id=accounts["customer"], claim_id=ids["claim"],
                                    type="evaluation", title="Done", message="x",
                                    dedupe_key="EVL-1"))
        db.session.commit()
        db.session.add(Notification(user_id=accounts["customer"], claim_id=ids["claim"],
                                    type="evaluation", title="Done", message="x",
                                    dedupe_key="EVL-1"))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()