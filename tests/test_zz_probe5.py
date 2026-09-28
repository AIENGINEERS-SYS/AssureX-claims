"""Temporary probe 5 - delete before finishing."""
from datetime import date, timedelta
import pytest
from sqlalchemy import select
from backend.db.models import Claim, Document, Product, Warranty
from backend.extensions import db
from backend.services.duplicate_detection import DuplicateDetectionService
from test_auth import app, client, accounts, claims, bearer, login  # noqa: F401

SEQ = iter(range(20000, 90000))


def test_probe_duplicate_full(app, claims, accounts):
    base = claims["customer"]["claim"]
    attach_same = None
    with app.app_context():
        # a document on the fixture claim whose hash will be reused by the second claim
        digest = f"{next(SEQ):064d}"
        db.session.add(Document(claim_id=base, uploaded_by=accounts["customer"], document_type="receipt",
            original_filename="r.png", stored_filename="r.png", mime_type="image/png", file_size=90,
            storage_path=f"x/{digest}.png", file_hash=digest, ocr_status="completed",
            review_status="confirmed",
            verified_data={"invoice_number": {"ocr_value": "INV-1", "confirmed_value": "INV-1",
                                              "was_corrected": False, "ocr_confidence": .9}}))
        db.session.commit()
        finding = DuplicateDetectionService().evaluate(db.session.get(Claim, base))
        print("\nbase:", finding["duplicate_risk"], finding["score"])
        for match in finding["matches"]:
            print("  ", match["claim_id"], match["score"],
                  {s["field"]: s["match"] for s in match["signals"] if s["match"]})

    with app.app_context():
        user = accounts["customer"]
        product = Product(user_id=user, name="Second", category="electronics", brand="Example",
            model_number="M1", serial_number="customer", purchase_date=date.today(),
            purchase_price=100, retailer="Shop")
        db.session.add(product)
        db.session.flush()
        warranty = Warranty(product_id=product.id, provider="Example", start_date=date.today(),
            expiry_date=date.today() + timedelta(days=365), coverage_duration_months=12)
        db.session.add(warranty)
        db.session.flush()
        second = Claim(user_id=user, product_id=product.id, warranty_id=warranty.id, fault_date=date.today(),
            fault_type="power", fault_description="No power", status="submitted",
            submission_date=date.today())
        db.session.add(second)
        db.session.commit()
        second_id = second.id
        with app.app_context():
            pass
        # invoice in nested verified_data form as the real code stores it
        db.session.add(Document(claim_id=second.id, uploaded_by=user, document_type="receipt",
            original_filename="r2.png", stored_filename="r2.png", mime_type="image/png", file_size=90,
            storage_path=f"x/{next(SEQ)}.png", file_hash=digest, ocr_status="completed",
            review_status="confirmed",
            verified_data={"invoice_number": {"ocr_value": "INV-1", "confirmed_value": "INV-1",
                                              "was_corrected": False, "ocr_confidence": .9}}))
        db.session.commit()
        finding = DuplicateDetectionService().evaluate(db.session.get(Claim, second_id))
        print("second (same hash+serial+user+date):", finding["duplicate_risk"], finding["score"])
        for match in finding["matches"]:
            print("  ", match["claim_id"], match["public_claim_id"], match["score"],
                  {s["field"]: s["match"] for s in match["signals"] if s["match"]})
        print("thresholds:", finding["thresholds"], "related:", finding["related_claim_ids"])


def test_probe_document_type_names(app, claims, accounts):
    print("\n--- stored document types vs policy requirements ---")
    with app.app_context():
        for name in ("damage_evidence", "fault_evidence"):
            try:
                db.session.add(Document(claim_id=claims["customer"]["claim"],
                    uploaded_by=accounts["customer"], document_type=name,
                    original_filename="x.png", stored_filename="x.png", mime_type="image/png",
                    file_size=1, storage_path="x/y.png", file_hash=f"{next(SEQ):064d}"))
                db.session.flush()
                print(f"  direct insert {name!r}: ACCEPTED")
            except Exception as exc:
                db.session.rollback()
                print(f"  direct insert {name!r}: REJECTED ({type(exc).__name__})")
        from backend.services.document_service import completeness, public_document_type, stored_document_type
        print("  stored_document_type('damage_evidence') =", stored_document_type("damage_evidence"))
        print("  public_document_type('fault_evidence') =", public_document_type("fault_evidence"))
