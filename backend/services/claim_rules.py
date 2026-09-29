"""Transparent warranty and evidence checks for one claim."""
import calendar
import re
from datetime import date

from backend.services.document_service import public_document_type


def normalized(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def add_months(value, months):
    month = value.month - 1 + months
    year, month = value.year + month // 12, month % 12 + 1
    return value.replace(year=year, month=month, day=min(value.day, calendar.monthrange(year, month)[1]))


def parsed_date(value):
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


class ClaimRuleEngine:
    def evaluate(self, claim, policy):
        documents = list(claim.documents)
        # Database storage uses "fault_evidence" while the public/API policy
        # vocabulary uses "damage_evidence". Canonicalize before comparing
        # against policy requirements so valid uploads are not falsely marked
        # missing.
        document_types = sorted({
            public_document_type(d.document_type) for d in documents
        })
        required_documents = tuple(
            dict.fromkeys(public_document_type(kind) for kind in policy.required_documents)
        )
        optional_documents = tuple(
            dict.fromkeys(public_document_type(kind) for kind in policy.optional_documents)
        )
        evidence = self._document_evidence(documents)
        results = []

        claim_date = claim.submission_date or (claim.submitted_at.date() if claim.submitted_at else None)
        expiry = add_months(claim.product.purchase_date, policy.warranty_months)
        if claim.warranty and claim.warranty.expiry_date:
            expiry = min(expiry, claim.warranty.expiry_date)
        active = claim_date is not None and claim_date <= expiry
        results.append(self._result("warranty_expiry", "Warranty expiry", "coverage",
            "passed" if active else "failed" if claim_date else "manual_review",
            "info" if active else "high", "Claim is within the warranty period." if active else
            "Claim date is missing." if not claim_date else "Warranty expired before this claim was submitted.",
            {"purchase_date": claim.product.purchase_date.isoformat(),
             "claim_date": claim_date.isoformat() if claim_date else None, "warranty_expiry": expiry.isoformat()}))

        serials = {"registered_product": claim.product.serial_number}
        models = {"registered_product": claim.product.model_number}
        dates = {"purchase_date_registered": claim.product.purchase_date}
        invoices = {}
        for source, values in evidence:
            for field, target in (("serial_number", serials), ("model_number", models),
                                  ("purchase_date", dates), ("invoice_number", invoices)):
                if values.get(field) not in (None, ""):
                    target[source] = values[field]
        serial_keys = {normalized(value) for value in serials.values() if normalized(value)}
        serial_status = "passed" if len(serial_keys) == 1 else "failed" if len(serial_keys) > 1 else "manual_review"
        results.append(self._result("serial_number_verification", "Serial number verification", "identity",
            serial_status, "info" if serial_status == "passed" else "high" if serial_status == "failed" else "medium",
            "Serial numbers agree across available evidence." if serial_status == "passed" else
            "Serial numbers conflict across evidence." if serial_status == "failed" else "No usable serial number was found.",
            {"sources": serials, "normalized_values": sorted(serial_keys)}))

        contradictions = []
        purchase_dates = [(source, parsed_date(value)) for source, value in dates.items()]
        purchase_dates = [(source, value) for source, value in purchase_dates if value]
        if claim_date:
            for source, value in purchase_dates:
                if value > claim_date:
                    contradictions.append(self._contradiction("purchase_after_claim", "high", [source, "claim"],
                        [value.isoformat(), claim_date.isoformat()]))
            if claim.fault_date and claim.fault_date > claim_date:
                contradictions.append(self._contradiction("fault_after_claim", "high", ["claim_form", "claim"],
                    [claim.fault_date.isoformat(), claim_date.isoformat()]))
        for repair in claim.repairs:
            if repair.repair_date < claim.product.purchase_date:
                contradictions.append(self._contradiction("repair_before_purchase", "high",
                    [f"repair:{repair.repair_id}", "registered_product"],
                    [repair.repair_date.isoformat(), claim.product.purchase_date.isoformat()]))
        if len(serial_keys) > 1:
            contradictions.append(self._contradiction("serial_number_mismatch", "high",
                list(serials), [str(value) for value in serials.values()]))
        model_keys = {normalized(value) for value in models.values() if normalized(value)}
        if len(model_keys) > 1:
            contradictions.append(self._contradiction("product_model_mismatch", "high",
                list(models), [str(value) for value in models.values()]))
        results.append(self._result("contradiction_detection", "Contradiction detection", "evidence",
            "failed" if contradictions else "passed", "high" if contradictions else "info",
            f"Found {len(contradictions)} material contradiction(s)." if contradictions else
            "No material contradictions were found.", {"findings": contradictions}))

        missing = sorted(set(required_documents) - set(document_types))
        results.append(self._result("missing_documents", "Required documents", "evidence",
            "manual_review" if missing else "passed", "medium" if missing else "info",
            "Mandatory evidence is missing." if missing else "All mandatory evidence is available.",
            {"required_documents": list(required_documents), "submitted_documents": document_types,
             "missing_documents": missing, "optional_documents": list(optional_documents)}))

        repairs = list(claim.repairs)
        if not repairs:
            repair_state, repair_result = "no_repair", "passed"
        elif any(not repair.authorized_service_center for repair in repairs):
            repair_state, repair_result = "unauthorized", "failed"
        elif all(repair.authorized_service_center for repair in repairs):
            repair_state, repair_result = "authorized", "passed"
        else:
            repair_state, repair_result = "unknown", "manual_review"
        if not policy.authorized_repair_required:
            repair_result = "passed"
        results.append(self._result("repair_authorization", "Repair authorization", "coverage", repair_result,
            "high" if repair_result == "failed" else "medium" if repair_result == "manual_review" else "info",
            {"authorized": "Repair was completed by an authorized center.", "unauthorized":
             "An unauthorized repair was confirmed.", "unknown": "Repair authorization could not be verified.",
             "no_repair": "No repair was recorded."}[repair_state],
            {"state": repair_state, "repair_ids": [r.repair_id for r in repairs],
             "invalidates_coverage": policy.unauthorized_repair_invalidates}))

        fault_text = " ".join(filter(None, (claim.fault_type, claim.fault_description, claim.damage_type))).casefold()
        matched = [exclusion for exclusion in policy.exclusions if self._phrase_match(exclusion, fault_text)]
        results.append(self._result("excluded_damage", "Excluded damage", "coverage",
            "failed" if matched else "passed", "high" if matched else "info",
            "Claim evidence matches a policy exclusion." if matched else "No configured damage exclusion matched.",
            {"matched_exclusions": matched, "evidence": fault_text[:1000]}))
        return results, contradictions

    @staticmethod
    def _document_evidence(documents):
        rows = []
        for document in documents:
            values = document.verified_data or document.extracted_data or {}
            if isinstance(values, dict):
                rows.append((f"document:{document.id}:{document.document_type}", values))
        return rows

    @staticmethod
    def _phrase_match(phrase, text):
        words = re.findall(r"[a-z0-9]+", phrase.casefold())
        haystack = set(re.findall(r"[a-z0-9]+", text))
        return bool(words) and all(word in haystack for word in words)

    @staticmethod
    def _contradiction(kind, severity, sources, values):
        return {"type": kind, "severity": severity, "sources": sources, "values": values}

    @staticmethod
    def _result(code, name, category, result, severity, message, evidence):
        return {"rule_code": code, "rule_name": name, "rule_category": category,
                "result": result, "passed": result == "passed", "severity": severity,
                "message": message, "evidence": evidence}
