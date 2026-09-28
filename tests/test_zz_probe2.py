"""Temporary probe 2 - delete before finishing."""
from datetime import date
import pytest
from backend.db.models import Claim, Document
from backend.extensions import db
from backend.services.claim_rules import ClaimRuleEngine
from backend.services.warranty_policy import WarrantyPolicyService
from test_auth import app, client, accounts, claims, bearer, login  # noqa: F401

FIELDS = ("purchase_date", "invoice_number", "product_name", "model_number", "serial_number",
          "retailer", "purchase_price", "warranty_duration")
KINDS = ("receipt", "product_image", "serial_number_image", "fault_evidence")


COUNTER = iter(range(1000, 9999))


def add_docs(app, claim_id, uploader, *, extracted=None, verified=None, prefix="p", kinds=KINDS):
    with app.app_context():
        made = []
        for kind in kinds:
            doc = Document(claim_id=claim_id, uploaded_by=uploader, document_type=kind,
                original_filename=f"{kind}.png", stored_filename=f"{kind}.png", mime_type="image/png",
                file_size=77, storage_path=f"x/{prefix}-{kind}.png", file_hash=f"{next(COUNTER):064d}",
                ocr_status="completed", review_status="confirmed",
                extracted_data=extracted, verified_data=verified)
            db.session.add(doc)
            made.append(doc)
        db.session.commit()
        return [doc.id for doc in made]


def summary(app, claim_id, today=None):
    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        claim.submission_date = claim.submission_date or date.today()
        policy = WarrantyPolicyService(app.config["WARRANTY_POLICY_PATH"]).for_product(claim.product)
        rules, contradictions = ClaimRuleEngine().evaluate(claim, policy)
        db.session.rollback()
        return ({r["rule_code"]: r["result"] for r in rules}, contradictions,
                [c["type"] for c in contradictions])


def test_probe_serial_matrix(app, claims, accounts):
    cid = claims["customer"]["claim"]
    print("\n--- no documents ---")
    print(summary(app, cid))

    print("\n--- 4 docs, extracted blank (value None) ---")
    blank = {f: {"value": None, "confidence": 0.0, "state": "not_detected"} for f in FIELDS}
    add_docs(app, cid, accounts["customer"], extracted=blank, prefix="a")
    print(summary(app, cid))

    print("\n--- verified_data plain string matching registered serial ---")
    with app.app_context():
        claim = db.session.get(Claim, cid)
        print("product serial:", claim.product.serial_number, "model:", claim.product.model_number)
    add_docs(app, cid, accounts["customer"], verified={"serial_number": "customer",
        "model_number": "M1"}, kinds=("serial_number_image",), prefix="b")
    print(summary(app, cid))

    print("\n--- verified_data structured dict (as real code stores) ---")
    add_docs(app, cid, accounts["customer"], verified={"serial_number": {
        "ocr_value": "customer", "confirmed_value": "customer", "was_corrected": False,
        "ocr_confidence": 0.7}}, kinds=("receipt",), prefix="c")
    print(summary(app, cid))

    print("\n--- verified_data mismatching serial ---")
    add_docs(app, cid, accounts["customer"], verified={"serial_number": "OTHER-SERIAL"},
        kinds=("damage_evidence",), prefix="d")
    print(summary(app, cid))


def test_probe_claim_category_rules(app, claims, accounts):
    """Mobile + appliances policy differences."""
    from backend.db.models import Product, Warranty
    for category, label in (("Smartphone", "mobile"), ("Refrigerator", "appliances"),
                            ("Solar Inverter", "unconfigured"), ("Electronics", "electronics")):
        with app.app_context():
            user = accounts["customer"]
            product = Product(user_id=user, name=f"n {label}", category=category, brand="B",
                model_number=f"M-{label}", serial_number=f"S-{label}-{category}",
                purchase_date=date.today(), purchase_price=100, retailer="R")
            db.session.add(product)
            db.session.flush()
            warranty = Warranty(product_id=product.id, provider="P", start_date=date.today(),
                expiry_date=date.today(), coverage_duration_months=24)
            db.session.add(warranty)
            db.session.flush()
            claim = Claim(user_id=user, product_id=product.id, warranty_id=warranty.id,
                fault_date=date.today(), fault_type="no_power", fault_description="It does not turn on",
                status="submitted", submission_date=date.today())
            db.session.add(claim)
            db.session.commit()
            cid = claim.id
        print(f"\n--- {category} ({label}) ---")
        print(summary(app, cid))
