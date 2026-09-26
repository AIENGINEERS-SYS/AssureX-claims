"""Document OCR orchestration, review preservation and completeness policy."""
from __future__ import annotations

from datetime import timezone
from decimal import Decimal
from time import monotonic
from flask import current_app
from sqlalchemy import select
from backend.db.models import Document, utcnow
from backend.extensions import db
from .document_extraction import FIELDS, detected_confidence, extract_fields, normalize_text
from .ocr_service import OCRProcessingError, get_ocr_provider


def process_document(document: Document, content: bytes, provider=None) -> str:
    """Process synchronously today; this boundary can be called by a future worker unchanged."""
    started = monotonic()
    document.ocr_status = "processing"
    document.ocr_error = None
    provider = provider or get_ocr_provider()
    try:
        result = provider.extract_text(content, document.mime_type)
        raw_text = normalize_text(result.text)
        extracted = extract_fields(raw_text, public_document_type(document.document_type))
        if result.confidence is not None:
            for field in extracted.values():
                if field.get("value") is not None:
                    field["confidence"] = round(min(float(field.get("confidence", 0)), result.confidence), 2)
        confidence = detected_confidence(extracted)
        document.ocr_text = raw_text
        document.extracted_data = extracted
        document.verified_data = None
        overall = result.confidence if result.confidence is not None else confidence
        document.ocr_confidence = Decimal(str(round(overall, 4))) if overall is not None else None
        document.ocr_provider = result.provider[:80]
        document.ocr_provider_version = (result.provider_version or "")[:80] or None
        detected = any(field.get("value") is not None for field in extracted.values())
        low = any(field.get("value") is not None and
                  float(field.get("confidence", 0)) < current_app.config["OCR_HIGH_CONFIDENCE_THRESHOLD"]
                  for field in extracted.values())
        if result.warnings:
            document.ocr_status = "completed_with_warnings"
        elif detected and low:
            document.ocr_status = "review_required"
        else:
            document.ocr_status = "completed"
        document.review_status = "pending" if detected else "not_required"
        return "ocr_completed"
    except OCRProcessingError as exc:
        document.ocr_status = "failed"
        document.ocr_error = str(exc)[:500]
        document.review_status = "not_required"
        document.extracted_data = {field: {"value": None, "confidence": 0.0, "state": "not_detected"}
                                   for field in FIELDS}
        return "ocr_failed"
    except Exception:
        current_app.logger.exception("OCR processing failed document_id=%s claim_id=%s", document.id, document.claim_id)
        document.ocr_status = "failed"
        document.ocr_error = "Document processing failed. Retry the analysis or continue for manual review."
        document.review_status = "not_required"
        return "ocr_failed"
    finally:
        document.ocr_processed_at = utcnow()
        document.processing_duration_ms = max(0, round((monotonic() - started) * 1000))


def public_document_type(value: str) -> str:
    return "damage_evidence" if value == "fault_evidence" else value


def stored_document_type(value: str) -> str:
    return "fault_evidence" if value == "damage_evidence" else value


def duplicate_on_other_claim(file_hash: str, claim_id: int) -> bool:
    return db.session.scalar(select(Document.id).where(Document.file_hash == file_hash,
        Document.claim_id.is_not(None), Document.claim_id != claim_id).limit(1)) is not None


def completeness(claim, required_types) -> dict:
    required = list(dict.fromkeys(required_types))
    uploaded = {public_document_type(document.document_type) for document in claim.documents}
    missing = [kind for kind in required if kind not in uploaded]
    return {"required": len(required), "uploaded": len(set(required) & uploaded), "missing": missing,
            "completeness_score": round((len(required) - len(missing)) / len(required), 2) if required else 1.0}


def confidence_label(confidence) -> str:
    if confidence is None:
        return "Not detected"
    value = float(confidence)
    if value >= current_app.config["OCR_HIGH_CONFIDENCE_THRESHOLD"]:
        return "High confidence"
    if value >= current_app.config["OCR_REVIEW_THRESHOLD"]:
        return "Needs review"
    return "Low confidence"


def document_json(document: Document, *, include_text=False, include_fraud=False) -> dict:
    def timestamp(value):
        return value.replace(tzinfo=timezone.utc).isoformat() if value else None
    extracted = {key: ({**value, "confidence_label": confidence_label(
                    value.get("confidence") if value.get("value") is not None else None)}
                if isinstance(value, dict) else value)
        for key, value in (document.extracted_data or {}).items()}
    result = {"id": document.id, "document_id": document.document_id,
        "document_type": public_document_type(document.document_type),
        "file_name": document.original_filename, "file_type": document.mime_type,
        "file_size": document.file_size, "uploaded_at": timestamp(document.created_at),
        "uploaded_by": document.uploaded_by, "upload_status": document.upload_status,
        "ocr_status": document.ocr_status, "ocr_error": document.ocr_error,
        "ocr_confidence": float(document.ocr_confidence) if document.ocr_confidence is not None else None,
        "ocr_confidence_label": confidence_label(document.ocr_confidence),
        "ocr_provider": document.ocr_provider, "ocr_provider_version": document.ocr_provider_version,
        "ocr_processed_at": timestamp(document.ocr_processed_at),
        "processing_duration_ms": document.processing_duration_ms,
        "extracted_data": extracted, "verified_data": document.verified_data or {},
        "review_status": document.review_status, "reviewed_at": timestamp(document.reviewed_at),
        "reviewed_by": document.reviewed_by}
    if include_fraud:
        result["cross_claim_duplicate"] = document.cross_claim_duplicate
    if include_text:
        result["ocr_raw_text"] = document.ocr_text or ""
    return result


def review_document(document: Document, corrections: dict, reviewer_id: int) -> bool:
    original = document.extracted_data or {}
    existing = document.verified_data or {}
    now = utcnow()
    verified, corrected = {}, False
    for field in FIELDS:
        extraction = original.get(field) or {"value": None, "confidence": 0.0, "state": "not_detected"}
        original_value = extraction.get("value")
        if field == "purchase_price" and isinstance(original_value, dict):
            original_value = original_value.get("amount")
        confirmed = corrections.get(field, existing.get(field, {}).get("confirmed_value", original_value))
        if field == "purchase_date" and hasattr(confirmed, "isoformat"):
            confirmed = confirmed.isoformat()
        if field == "purchase_price" and confirmed is not None:
            value = Decimal(str(confirmed))
            confirmed = int(value) if value == value.to_integral() else float(value)
        if field == "warranty_duration":
            unit = corrections.get("warranty_duration_unit", extraction.get("unit")) if confirmed is not None else None
        else:
            unit = None
        changed = confirmed != original_value or (field == "warranty_duration" and unit != extraction.get("unit"))
        corrected = corrected or changed
        item = {"ocr_value": original_value, "confirmed_value": confirmed, "was_corrected": changed,
                "ocr_confidence": extraction.get("confidence", 0.0)}
        if unit:
            item["unit"] = unit
        if changed:
            item.update({"corrected_by": reviewer_id, "corrected_at": now.isoformat()})
        verified[field] = item
    document.verified_data = verified
    document.review_status = "confirmed"
    document.reviewed_at = now
    document.reviewed_by = reviewer_id
    return corrected
