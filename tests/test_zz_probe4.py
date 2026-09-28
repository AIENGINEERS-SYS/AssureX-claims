"""Temporary probe 4 - delete before finishing."""
from datetime import date, timedelta
import pytest
from backend.db.models import Claim, Document, Product, RepairHistory, Warranty
from backend.extensions import db
from backend.services.claim_rules import ClaimRuleEngine
from backend.services.decision_engine import DecisionEngine
from backend.services.duplicate_detection import DuplicateDetectionService
from backend.services.warranty_policy import WarrantyPolicyService
from test_auth import app, client, accounts, claims, bearer, login  # noqa: F401

SEQ = iter(range(2000, 9000))


def policy_of(app, product):
    return WarrantyPolicyService(app.config["WARRANTY_POLICY_PATH"]).for_product(product)


def attach_docs(app, claim_id, uploader, *, verified=None, kinds=("receipt", "product_image",
        "serial_number_image", "damage_evidence")):
    with app.app_context():
        for kind in kinds:
            db.session.add(Document(claim_id=claim_id, uploaded_by=uploader,
                document_type="fault_evidence" if kind == "damage_evidence" else kind,
                original_filename=f"{kind}.png", stored_filename=f"{kind}.png", mime_type="image/png",
                file_size=90, storage_path=f"x/{next(SEQ)}-{kind}.png", file_hash=f"{next(SEQ):064d}",
                ocr_status="completed", review_status="confirmed", verified_data=verified))
        db.session.commit()


def rules_of(app, claim_id):
    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        policy = policy_of(app, claim.product)
        rules, contradictions = ClaimRuleEngine().evaluate(claim, policy)
        return {r["rule_code"]: r for r in rules}, contradictions


def test_probe_repair_and_exclusion(app, claims, accounts):
    cid = claims["customer"]["claim"]
    print("\n--- base (4 docs, no extraction) ---")
    attach_docs(app, cid, accounts["customer"])
    rules, contra = rules_of(app, cid)
    print({k: v["result"] for k, v in rules.items()})

    print("\n--- unauthorized repair ---")
    with app.app_context():
        product = db.session.get(Claim, cid).product
        db.session.add(RepairHistory(product_id=product.id, claim_id=cid, repair_date=date.today(),
            service_center_name="Local Shop", authorized_service_center=False, repair_outcome="replaced",
            repair_cost=5000, notes=""))
        db.session.commit()
    rules, contra = rules_of(app, cid)
    print("repair:", rules["repair_authorization"]["result"], rules["repair_authorization"]["evidence"])

    print("\n--- authorized repair ---")
    with app.app_context():
        for repair in db.session.scalars(__import__("sqlalchemy").select(RepairHistory)).all():
            repair.authorized_service_center = True
        db.session.commit()
    rules, contra = rules_of(app, cid)
    print("repair:", rules["repair_authorization"]["result"], rules["repair_authorization"]["evidence"]["state"])


def test_probe_excluded_damage(app, claims, accounts):
    for text, fault in (("Water damage to the board after rainfall", "water_damage"),
                        ("Unit was physically impacted during a fall", "impact"),
                        ("Owner reported misuse of the appliance", "misuse"),
                        ("Connected to the wrong voltage supply", "voltage"),
                        ("Screen stopped turning on after power outage", "no_power")):
        with app.app_context():
            claim = db.session.get(Claim, claims["customer"]["claim"])
            claim.fault_description, claim.fault_type, claim.damage_type = text, fault, None
            db.session.commit()
        rules, _ = rules_of(app, claims["customer"]["claim"])
        print(f"{fault:16} -> {rules['excluded_damage']['result']:8}",
              rules["excluded_damage"]["evidence"].get("matched_exclusions"))


def test_probe_warranty_boundaries(app, claims):
    print("\n--- warranty expiry boundaries (electronics policy = 24 months) ---")
    for delta, claim_delta, label in ((-1, 5, "expired 1 day before claim"),
                                      (0, 0, "expires exactly on claim date"),
                                      (1, 0, "expires 1 day after claim"),
                                      (-730, 5, "expired long ago"),
                                      (-730, None, "expired, no claim date")):
        with app.app_context():
            claim = db.session.get(Claim, claims["customer"]["claim"])
            claim.product.purchase_date = date.today() + timedelta(days=delta)
            claim.submission_date = None if claim_delta is None else date.today() + timedelta(days=claim_delta)
            claim.warranty.expiry_date = date.today() + timedelta(days=10000)
            db.session.commit()
        rules, _ = rules_of(app, claims["customer"]["claim"])
        print(f"{label:32} -> {rules['warranty_expiry']['result']:13} {rules['warranty_expiry']['evidence']['warranty_expiry']}")


def test_probe_duplicate(app, claims, accounts):
    print("\n--- duplicate detection ---")
    base = claims["customer"]["claim"]
    with app.app_context():
        finding = DuplicateDetectionService().evaluate(db.session.get(Claim, base))
        print("fixture customer claim:", finding["duplicate_risk"], finding["score"],
              [(m["claim_id"], m["score"]) for m in finding["matches"]])

    # second claim, same user, different product, identical description + serial
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
        finding = DuplicateDetectionService().evaluate(second)
        print("second vs first:", finding["duplicate_risk"], finding["score"])
        for match in finding["matches"]:
            print("  match", match["claim_id"], match["score"],
                  {s["field"]: s["match"] for s in match["signals"]})


def test_probe_decision_engine_reaches_valid(app):
    with app.app_context():
        service_rules = [{"rule_code": code, "rule_name": code, "rule_category": "c", "result": "passed",
                          "passed": True, "severity": "info", "message": "ok", "evidence": {}}
                         for code in ("warranty_expiry", "serial_number_verification", "contradiction_detection",
                                      "missing_documents", "repair_authorization", "excluded_damage")]
        out = {"prediction_class": "valid", "top_confidence": .9, "confidence":
               {"valid": .9, "invalid": .05, "manual_review": .05}}
        decision, explanation = DecisionEngine().decide(out, dict(out),
            {"status": "Strong Match"}, service_rules, [], {"duplicate_risk": "low"})
        print("\nall-pass ->", decision, explanation["problems"])
        decision, _ = DecisionEngine().decide(out, dict(out), {"status": "Strong Match"},
            service_rules, [], {"duplicate_risk": "medium"})
        print("dup medium ->", decision)
        decision, _ = DecisionEngine().decide(out, dict(out), {"status": "Strong Match"},
            service_rules, [], {"duplicate_risk": "low"})
        for rule in service_rules:
            if rule["rule_code"] == "missing_documents":
                rule.update(result="manual_review", passed=False, severity="medium")
        print("missing docs ->", DecisionEngine().decide(out, dict(out), {"status": "Strong Match"},
            service_rules, [], {"duplicate_risk": "low"})[0])
