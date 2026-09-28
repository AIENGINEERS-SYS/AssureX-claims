"""Shared helpers for the fuzzy.txt scenario suite."""
from datetime import date, timedelta
import itertools

from backend.db.models import Claim, Document, Product, RepairHistory, Warranty
from backend.extensions import db
from backend.services.claim_rules import ClaimRuleEngine
from backend.services.warranty_policy import WarrantyPolicyService

IDS = itertools.count(500000)
HASHES = itertools.count(1)

# Public upload vocabulary accepted by the API layer.
REQUIRED_UPLOADS = ("receipt", "product_image", "serial_number_image", "damage_evidence")
# Database vocabulary enforced by ck_documents_type.
STORED_TYPE = {"damage_evidence": "fault_evidence"}


def auth(client, role="customer"):
    from test_auth import bearer, login
    return bearer(login(client, role)["access_token"])


def policy_for(app, claim):
    return WarrantyPolicyService(app.config["WARRANTY_POLICY_PATH"]).for_product(claim.product)


def evaluate_rules(app, claim_id):
    """Run the rule engine and return results keyed by rule_code plus contradictions."""
    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        policy = policy_for(app, claim)
        results, contradictions = ClaimRuleEngine().evaluate(claim, policy)
        return {item["rule_code"]: item for item in results}, contradictions


def add_document(app, claim_id, uploader, kind, *, verified=None, extracted=None,
                 document_type=None, status="confirmed"):
    """Insert a document directly, translating the public upload name to the stored name."""
    with app.app_context():
        row = Document(claim_id=claim_id, uploaded_by=uploader,
            document_type=document_type or STORED_TYPE.get(kind, kind),
            original_filename=f"{kind}.png", stored_filename=f"{next(IDS)}-{kind}.png",
            mime_type="image/png", file_size=1024,
            storage_path=f"fuzzy/{next(IDS)}-{kind}.png",
            file_hash=f"{next(HASHES):064d}", upload_status="stored", ocr_status="completed",
            review_status=status, verified_data=verified, extracted_data=extracted)
        db.session.add(row)
        db.session.commit()
        return row.document_id


def attach_evidence(app, claim_id, uploader, *, kinds=REQUIRED_UPLOADS, payload=None):
    """Attach one document per kind. ``payload`` overrides verified_data per kind."""
    for kind in kinds:
        data = None if payload is None else payload.get(kind)
        add_document(app, claim_id, uploader, kind, verified=data)


def confirmed_field(value, corrected=False):
    """Shape OCR/reviewer data the way backend.services.document_service stores it."""
    return {"ocr_value": value, "confirmed_value": value, "was_corrected": corrected,
            "ocr_confidence": 0.93}


def set_dates(app, claim_id, *, purchase_date=None, submission_date=None, claim_date_value=None,
              fault_date=None, expiry_date=None, start_date=None, clear_claim_date=False):
    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        if purchase_date is not None:
            claim.product.purchase_date = purchase_date
        if claim.warranty is not None:
            if start_date is not None:
                claim.warranty.start_date = start_date
            if expiry_date is not None:
                claim.warranty.expiry_date = expiry_date
        if clear_claim_date:
            claim.submission_date, claim.submitted_at = None, None
        elif claim_date_value is not None:
            claim.submission_date = claim_date_value
        if submission_date is not None:
            claim.submitted_at = submission_date
        if fault_date is not None:
            claim.fault_date = fault_date
        db.session.commit()


def add_repair(app, claim_id, *, repair_date=None, authorized=False, cost=25000, outcome="replaced"):
    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        row = RepairHistory(product_id=claim.product_id, claim_id=claim.id,
            repair_date=repair_date or date.today(),
            service_center_name="Local Repair Shop", authorized_service_center=authorized,
            repair_outcome=outcome, repair_cost=cost, notes="Board replaced.")
        db.session.add(row)
        db.session.commit()
        return row.repair_id


def set_fault(app, claim_id, *, description=None, fault_type=None, damage_type=None):
    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        if description is not None:
            claim.fault_description = description
        if fault_type is not None:
            claim.fault_type = fault_type
        if damage_type is not None:
            claim.damage_type = damage_type
        db.session.commit()


def seed_claim(app, *, user_id, category="electronics", purchase_date=None, submission_date=None,
               product_id=None, warranty_id=None, serial=None, model="M-1", description="No power",
               fault_type="power", status="submitted", clear_claim_date=False):
    """Create a product/warranty/claim triple directly, for rule-level scenarios."""
    with app.app_context():
        product = Product(user_id=user_id, name="Fuzzy Device", category=category, brand="Example",
            model_number=model, serial_number=serial or f"SN-{next(IDS)}",
            purchase_date=purchase_date or date.today(), purchase_price=150000, retailer="Shop")
        db.session.add(product)
        db.session.flush()
        warranty = Warranty(product_id=product.id, provider="Example",
            start_date=product.purchase_date, expiry_date=product.purchase_date + timedelta(days=730),
            coverage_duration_months=24)
        db.session.add(warranty)
        db.session.flush()
        claim = Claim(user_id=user_id, product_id=product.id, warranty_id=warranty.id,
            fault_date=date.today(), fault_type=fault_type, fault_description=description,
            status=status,
            submission_date=None if clear_claim_date else (submission_date or date.today()))
        db.session.add(claim)
        db.session.commit()
        return {"claim": claim.id, "product": product.id, "warranty": warranty.id}
