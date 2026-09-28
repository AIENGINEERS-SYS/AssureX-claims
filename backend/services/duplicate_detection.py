"""Evidence-based claim duplicate detection with database candidate narrowing."""
from difflib import SequenceMatcher
from flask import current_app
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import selectinload
from backend.db.models import Claim, Document, Product
from backend.extensions import db
from .claim_rules import normalized


class DuplicateDetectionService:
    WEIGHTS = {
        "document_hash": .35,
        "serial_number": .25,
        "invoice_number": .15,
        "claimant": .10,
        "product_model": .05,
        "fault_description": .07,
        "claim_date": .03,
    }

    def evaluate(self, claim):
        hashes = {d.file_hash for d in claim.documents if d.file_hash}
        matching_hash_claims = select(Document.claim_id).where(Document.file_hash.in_(hashes)) if hashes else None
        conditions = [Claim.user_id == claim.user_id]
        if claim.product_id:
            conditions.append(Claim.product_id == claim.product_id)
            conditions.append(and_(Product.brand == claim.product.brand,
                                   Product.model_number == claim.product.model_number))
        if matching_hash_claims is not None:
            conditions.append(Claim.id.in_(matching_hash_claims))
        query = (select(Claim).join(Product, Claim.product_id == Product.id)
                 .where(Claim.id != claim.id, Claim.status != "draft", or_(*conditions))
                 .options(selectinload(Claim.documents), selectinload(Claim.product)).limit(100))
        candidates = db.session.scalars(query).unique().all()
        findings = [self._compare(claim, other) for other in candidates]
        findings = [item for item in findings if item["score"] > 0]
        findings.sort(key=lambda item: (-item["score"], item["claim_id"]))
        top = findings[0]["score"] if findings else 0.0
        high = current_app.config["DUPLICATE_HIGH_THRESHOLD"]
        medium = current_app.config["DUPLICATE_MEDIUM_THRESHOLD"]
        risk = "high" if top >= high else "medium" if top >= medium else "low"
        return {"duplicate_risk": risk, "score": round(top, 4), "matches": findings[:10],
                "related_claim_ids": [item["claim_id"] for item in findings[:10]],
                "thresholds": {"medium": medium, "high": high}}

    def _compare(self, claim, other):
        claim_hashes = {d.file_hash for d in claim.documents if d.file_hash}
        other_hashes = {d.file_hash for d in other.documents if d.file_hash}
        left_invoice, right_invoice = self._invoices(claim), self._invoices(other)
        serial_match = bool(claim.product and other.product and
            normalized(claim.product.serial_number) == normalized(other.product.serial_number))
        model_match = bool(claim.product and other.product and
            normalized(claim.product.model_number) == normalized(other.product.model_number) and
            normalized(claim.product.brand) == normalized(other.product.brand))
        description_similarity = SequenceMatcher(None, normalized(claim.fault_description),
                                                   normalized(other.fault_description)).ratio()
        date_match = bool(claim.submission_date and other.submission_date and
                          abs((claim.submission_date - other.submission_date).days) <= 30)
        raw = {
            "document_hash": bool(claim_hashes & other_hashes),
            "serial_number": serial_match,
            "invoice_number": bool(left_invoice & right_invoice),
            "claimant": claim.user_id == other.user_id,
            "product_model": model_match,
            "fault_description": description_similarity >= current_app.config["DUPLICATE_DESCRIPTION_THRESHOLD"],
            "claim_date": date_match,
        }
        score = sum(self.WEIGHTS[field] for field, match in raw.items() if match)
        signals = [{"field": field, "match": match,
                    **({"similarity": round(description_similarity, 4)} if field == "fault_description" else {})}
                   for field, match in raw.items()]
        return {"claim_id": other.id, "public_claim_id": other.claim_id,
                "score": round(min(score, 1.0), 4), "signals": signals}

    @staticmethod
    def _invoices(claim):
        values = set()
        for document in claim.documents:
            evidence = document.verified_data or document.extracted_data or {}
            if isinstance(evidence, dict) and normalized(evidence.get("invoice_number")):
                values.add(normalized(evidence["invoice_number"]))
        return values
