"""Temporary probe 6 - delete before finishing."""
from datetime import date, timedelta
from backend.db.models import Claim, Document, Product, Warranty
from backend.extensions import db
from backend.services.duplicate_detection import DuplicateDetectionService
from test_auth import app, client, accounts, claims  # noqa: F401

SEQ = iter(range(30000, 90000))


def test_probe_duplicate_high(app, claims, accounts):
    base = claims["customer"]["claim"]
    with app.app_context():
        digest = f"{next(SEQ):064d}"
        db.session.add(Document(claim_id=base, uploaded_by=accounts["customer"], document_type="receipt",
            original_filename="r.png", stored_filename="r.png", mime_type="image/png", file_size=90,
            storage_path=f"x/{digest}.png", file_hash=digest, ocr_status="completed",
            review_status="confirmed",
            verified_data={"invoice_number": {"ocr_value": "INV-1", "confirmed_value": "INV-1",
                                              "was_corrected": False, "ocr_confidence": .9}}))
        user = accounts["customer"]
        product = db.session.get(Product, claims["customer"]["product"])
        second = Claim(user_id=user, product_id=product.id, warranty_id=claims["customer"]["warranty"],
            fault_date=date.today(), fault_type="power", fault_description="No power",
            status="submitted", submission_date=date.today())
        db.session.add(second)
        db.session.flush()
        db.session.add(Document(claim_id=second.id, uploaded_by=user, document_type="receipt",
            original_filename="r2.png", stored_filename="r2.png", mime_type="image/png", file_size=90,
            storage_path=f"x/{next(SEQ)}.png", file_hash=digest, ocr_status="completed",
            review_status="confirmed",
            verified_data={"invoice_number": {"ocr_value": "INV-1", "confirmed_value": "INV-1",
                                              "was_corrected": False, "ocr_confidence": .9}}))
        db.session.commit()
        second_id = second.id

    with app.app_context():
        finding = DuplicateDetectionService().evaluate(db.session.get(Claim, second_id))
        print("\nSECOND:", finding["duplicate_risk"], finding["score"])
        for match in finding["matches"]:
            print("  ", match["claim_id"], match["score"],
                  {s["field"]: s["match"] for s in match["signals"] if s["match"]})
        print("  thresholds:", finding["thresholds"])
