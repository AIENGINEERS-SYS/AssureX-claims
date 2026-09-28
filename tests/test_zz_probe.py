"""Temporary probe - delete before finishing."""
from datetime import date
import pytest
from backend.db.models import Claim, Document, RepairHistory
from backend.extensions import db
from backend.services.claim_rules import ClaimRuleEngine
from backend.services.warranty_policy import WarrantyPolicyService
from test_auth import app, client, accounts, claims, bearer, login  # noqa: F401
from test_claim_submission import png


def test_probe_serial_rule(client, app, headers=None):
    with app.app_context():
        claim = db.session.get(Claim, claims["customer"]["claim"])
        claim.submission_date = date.today()
        policy = WarrantyPolicyService(app.config["WARRANTY_POLICY_PATH"]).for_product(claim.product)
        # 1. no documents at all
        rules, contradictions = ClaimRuleEngine().evaluate(claim, policy)
        print("\nNO DOCS:", {r["rule_code"]: r["result"] for r in rules})

        # 2. document with no extraction data
        doc = Document(claim_id=claim.id, uploaded_by=claim.user_id, document_type="receipt",
            original_filename="r.png", stored_filename="r.png", mime_type="image/png",
            file_size=1, storage_path="x/r.png", file_hash="a" * 64)
        db.session.add(doc)
        db.session.commit()
        rules, contradictions = ClaimRuleEngine().evaluate(claim, policy)
        print("NO EXTRACTION:", {r["rule_code"]: r["result"] for r in rules})

        # 3. document with disabled-OCR style extracted_data
        doc.extracted_data = {f: {"value": None, "confidence": 0.0, "state": "not_detected"}
                              for f in ("purchase_date", "invoice_number", "product_name",
                                        "model_number", "serial_number", "retailer",
                                        "purchase_price", "warranty_duration")}
        db.session.commit()
        rules, contradictions = ClaimRuleEngine().evaluate(claim, policy)
        print("BLANK EXTRACTION:", {r["rule_code"]: r["result"] for r in rules})

        # 4. verified_data with matching serial
        doc.verified_data = {"serial_number": {"ocr_value": "customer", "confirmed_value": "customer",
                                               "was_corrected": False, "ocr_confidence": 0.7}}
        db.session.commit()
        rules, contradictions = ClaimRuleEngine().evaluate(claim, policy)
        print("VERIFIED MATCH:", {r["rule_code"]: r["result"] for r in rules})

        # 5. verified_data plain string
        doc.verified_data = {"serial_number": "customer"}
        db.session.commit()
        rules, contradictions = ClaimRuleEngine().evaluate(claim, policy)
        print("VERIFIED PLAIN:", {r["rule_code"]: r["result"] for r in rules})
        print("EVIDENCE:", [r for r in rules if r["rule_code"] == "serial_number_verification"][0]["evidence"])
        print("POLICY:", policy.code, policy.warranty_months, policy.required_documents)


def test_probe_ocr_disabled_documents(app, claims):
    with app.app_context():
        claim = db.session.get(Claim, claims["customer"]["claim"])
        for i, kind in enumerate(("receipt", "product_image", "serial_number_image", "damage_evidence")):
            db.session.add(Document(claim_id=claim.id, uploaded_by=claim.user_id, document_type=kind,
                original_filename=f"{kind}.png", stored_filename=f"{kind}.png", mime_type="image/png",
                file_size=len(png()), storage_path=f"x/{kind}.png", file_hash=str(i) * 64,
                ocr_status="failed", review_status="not_required",
                extracted_data={f: {"value": None, "confidence": 0.0, "state": "not_detected"}
                                for f in ("purchase_date", "invoice_number", "product_name",
                                          "model_number", "serial_number", "retailer",
                                          "purchase_price", "warranty_duration")}))
        claim.submission_date = date.today()
        db.session.commit()
        policy = WarrantyPolicyService(app.config["WARRANTY_POLICY_PATH"]).for_product(claim.product)
        rules, contradictions = ClaimRuleEngine().evaluate(claim, policy)
        print("\nFAILED-OCR 4 DOCS:", {r["rule_code"]: r["result"] for r in rules})
        print("REQUIRED:", policy.required_documents)


def test_probe_product_category_mapping(app, claims):
    with app.app_context():
        service = WarrantyPolicyService(app.config["WARRANTY_POLICY_PATH"])
        from backend.db.models import Product
        for category in ("electronics", "Laptop", "Smartphone", "Refrigerator", "Solar Inverter",
                         "Smart Watch", "TWS Earbuds", "Home appliances", "Power tools", "Wearables"):
            product = Product(user_id=claims["customer"]["claim"] and 1, name="n", category=category,
                brand="b", model_number="m", serial_number=f"s-{category}", purchase_date=date.today(),
                purchase_price=1, retailer="r")
            try:
                print(category, "->", service.for_product(product).code)
            except Exception as exc:
                print(category, "-> ERROR", exc)
