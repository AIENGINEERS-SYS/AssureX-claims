"""Build customer-safe reports from frozen inputs, never from later live evidence."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from sqlalchemy import select
from backend.db.assessment_models import AssessmentReport
from backend.db.models import Review, utcnow
from backend.extensions import db
from .evaluation import prediction_json
from .report_exports import write_csv, write_excel, write_pdf


def values(source, keys):
    return {key: source.get(key) for key in keys.split()}


def human_decision(claim_id):
    from .claim_submission import timestamp
    review = db.session.scalar(select(Review).where(Review.claim_id == claim_id,
        Review.decision.in_(("approve", "reject"))).order_by(Review.id.desc()).limit(1))
    return ({"review_id": review.review_id, "decision": review.decision,
             "recorded_at": timestamp(review.reviewed_at)} if review else None)


def customer_explanation(explanation):
    # No raw model errors, duplicate claim IDs, reviewer notes or rule evidence.
    return {key: explanation[key] for key in ("decision", "decision_class", "required_action") if key in explanation} | {
        key: [values(item, "code message severity") for item in explanation.get(key, [])]
        for key in ("supporting_evidence", "problems")
    }


def build_payload(job, evaluation, claim):
    source, result = job.evidence_snapshot, job.evaluation_snapshot
    documents = []
    fact_keys = "product_name brand model_number serial_number purchase_date purchase_price retailer invoice_number warranty_duration"
    for document in source["documents"]:
        documents.append(values(document, "document_id document_type original_filename file_hash ocr_status review_status ocr_confidence") | {
            "extracted_facts": {key: values(value, "value confidence unit") if isinstance(value, dict) else value
                for key, value in (document.get("extracted_data") or {}).items() if key in fact_keys.split()},
            "customer_confirmed_facts": {key: values(value, "confirmed_value was_corrected unit") if isinstance(value, dict) else value
                for key, value in (document.get("verified_data") or {}).items() if key in fact_keys.split()},
            "provenance": "customer_confirmed" if document.get("review_status") == "confirmed" else
                "image_analysis" if document["document_type"] in {"product_image", "fault_evidence"} else "ocr_extraction",
            "analysis_findings": values(document.get("processing_metadata") or {}, "quality needs_review"),
            "image_predictions": {key: values(value, "value confidence state")
                for key, value in ((document.get("processing_metadata") or {}).get("fields") or {}).items()
                if key in {"product_name", "product_category", "brand", "model_number", "serial_number",
                           "damage_category", "description", "affected_components", "severity"} and isinstance(value, dict)},
        })
    evidence_keys = {"required_documents", "submitted_documents", "missing_documents", "missing",
        "purchase_date", "claim_date", "start_date", "expiry_date", "effective_expiry_date", "coverage_months"}
    rules = [values(rule, "rule_code rule_name rule_category result severity message") | {
        "supporting_facts": {key: value for key, value in rule.get("evidence", {}).items() if key in evidence_keys}}
        for rule in result["rules"]]
    return {
        "claim": source["claim"], "product": {key: value for key, value in source["product"].items() if key != "purchase_metadata"}, "warranty": source["warranty"],
        "missing_information": [key for key, value in source["product"].items() if value in (None, "") and key != "purchase_metadata"],
        "purchase_provenance": {key: values(value, "confirmed_value provenance source_evidence_id confirmed_at")
            for key, value in (source["product"].get("purchase_metadata") or {}).items() if isinstance(value, dict)},
        "product_information_provenance": "Registered product details. Evidence facts show OCR observations and customer confirmations; these do not independently verify identity or authenticity.",
        "evidence_inventory": documents,
        "image_analysis": {"method": "GTM classification of a structured claim summary card",
            "limitations": "The GTM model does not inspect the submitted damage photograph directly. Its output is a prediction, not verification of damage or coverage."},
        "policy": result["policy"], "rule_findings": rules,
        "model_predictions": {"python": prediction_json(evaluation.python_prediction),
                              "gtm": prediction_json(evaluation.gtm_prediction)},
        "model_agreement": values(evaluation.comparison, "status confidence_difference classes_match"),
        "duplicate_indicators": {"risk": evaluation.duplicate_finding.get("duplicate_risk"),
            "message": "Potential duplicate evidence requires staff review." if evaluation.duplicate_finding.get("duplicate_risk") in {"medium", "high"} else "No elevated duplicate indicator was recorded."},
        "contradictions": [values(item, "type severity message") for item in evaluation.contradictions],
        "automated_recommendation": evaluation.recommendation,
        "explanation": customer_explanation(evaluation.explanation),
        "follow_up": [rule["message"] for rule in rules if rule["result"] != "passed"],
        "human_review_status": "Awaiting authorized review" if not human_decision(claim.id) else "Decision recorded",
        "authorized_decision": human_decision(claim.id),
        "assessment_id": job.id, "assessment_version": job.version,
        "evaluation_id": evaluation.evaluation_id, "evidence_digest": job.evidence_digest,
        "generated_at": utcnow().isoformat(),
        "audit_references": result.get("audit_references", []),
        "notice": "This automated assessment is a recommendation. Approval or rejection requires an authorized human decision.",
    }


def generate(job, evaluation, claim):
    existing = db.session.scalar(select(AssessmentReport).where(AssessmentReport.assessment_id == job.id))
    if existing:
        return existing
    report = AssessmentReport(assessment_id=job.id, claim_id=job.claim_id,
        evaluation_id=evaluation.id, version=job.version, evidence_digest=job.evidence_digest,
        payload=build_payload(job, evaluation, claim))
    db.session.add(report)
    db.session.flush()
    return report


def export_bytes(report, format):
    """Reuse the existing export writers with the exact persisted customer payload."""
    writers = {"pdf": write_pdf, "csv": write_csv, "excel": write_excel}
    # Spools and exports are temporary; report JSON is durable and never expires.
    with TemporaryDirectory(prefix="assurex-assessment-") as directory:
        root = Path(directory)
        specs = {}
        for index, (name, value) in enumerate(report.payload.items()):
            path = root / f"{index}.jsonl"
            rows = [{"finding": item} for item in value] if isinstance(value, list) else [{"finding": value}]
            path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
            specs[name.replace("_", " ")[:31]] = {"path": path, "columns": ["finding"], "count": len(rows),
                "dates": set(), "widths": {"finding": 65}, "long_columns": {"finding": max(1,
                    max((len(json.dumps(row["finding"], ensure_ascii=False)) + 31999) // 32000 for row in rows) if rows else 1)}}
        output = root / "report"
        writers[format](output, specs, lambda: None)
        return output.read_bytes()
