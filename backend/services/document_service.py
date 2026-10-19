"""Document OCR orchestration, review preservation and completeness policy."""
from __future__ import annotations

from datetime import timezone
from decimal import Decimal
from time import monotonic
from flask import current_app
from sqlalchemy import select
from backend.db.models import Document, utcnow
from backend.extensions import db
from .document_extraction import detected_confidence, extract_fields, normalize_text
from .ocr_service import OCRProcessingError, get_ocr_provider
from .evidence import applicable_fields, category, visual_assessment


def process_document(document: Document, content: bytes, provider=None, *, force=False) -> str:
    """Process synchronously today; this boundary can be called by a future worker unchanged."""
    if document.ocr_processed_at and not force:
        return "ocr_failed" if document.ocr_status == "failed" else "ocr_completed"
    started = monotonic()
    document.ocr_status = "processing"
    document.ocr_error = None
    # Confirmed observations survive retries and provider failures.
    document.processing_metadata = {"warnings": []}
    fields = applicable_fields(document.document_type)
    try:
        visual = None
        if category(document.document_type) in {"PRODUCT_IMAGE", "DAMAGE_EVIDENCE"}:
            visual = visual_assessment(content, document.mime_type, document.document_type)
            document.processing_metadata = {**visual, "warnings": list(visual["warnings"])}
        extracted, raw_text = {}, ""
        result = None
        if fields:
            provider = provider or get_ocr_provider()
            try:
                result = provider.extract_text(content, document.mime_type)
            except OCRProcessingError:
                # Visible labels are optional on product photographs.
                if category(document.document_type) != "PRODUCT_IMAGE":
                    raise
                document.processing_metadata = {**document.processing_metadata,
                    "warnings": [*document.processing_metadata.get("warnings", []),
                        "Visible model and serial labels could not be read automatically."]}
            if result:
                raw_text = normalize_text(result.text)
                extracted = extract_fields(raw_text, public_document_type(document.document_type))
                if result.confidence is not None:
                    for field in extracted.values():
                        if field.get("value") is not None:
                            field["confidence"] = round(min(float(field.get("confidence", 0)), result.confidence), 2)
        confidence = detected_confidence(extracted)
        document.ocr_text = raw_text
        document.extracted_data = extracted
        overall = result.confidence if result and result.confidence is not None else confidence
        document.ocr_confidence = Decimal(str(round(overall, 4))) if overall is not None else None
        document.ocr_provider = (result.provider if result else visual["provider"] if visual else "secure-storage")[:80]
        document.ocr_provider_version = ((result.provider_version if result else visual["version"] if visual else "1") or "")[:80] or None
        warnings = list(document.processing_metadata.get("warnings", []))
        if result:
            warnings.extend(result.warnings)
        detected = any(field.get("value") is not None for field in extracted.values())
        uncertain = any(field.get("state") == "ambiguous" or
            (field.get("value") is not None and float(field.get("confidence", 0)) <
             current_app.config["OCR_HIGH_CONFIDENCE_THRESHOLD"]) for field in extracted.values())
        if fields and not detected and not visual:
            warnings.append("No usable details were extracted. Enter corrections or continue for manual review.")
        document.processing_metadata = {**document.processing_metadata, "warnings": warnings}
        if warnings:
            document.ocr_status = "completed_with_warnings"
        elif uncertain or (visual and visual.get("needs_review", True)):
            document.ocr_status = "review_required"
        else:
            document.ocr_status = "completed"
        document.review_status = "confirmed" if document.reviewed_at else "pending" if detected else "not_required"
        return "ocr_completed"
    except OCRProcessingError as exc:
        document.ocr_status = "failed"
        document.ocr_error = str(exc)[:500]
        document.review_status = "confirmed" if document.reviewed_at else "not_required"
        # Keep the previous successful extraction and all human confirmations.
        if document.extracted_data is None:
            document.extracted_data = {field: {"value": None, "confidence": 0.0, "state": "not_detected"}
                                       for field in fields}
        return "ocr_failed"
    except Exception:
        current_app.logger.exception("Evidence processing failed document_id=%s claim_id=%s", document.id, document.claim_id)
        document.ocr_status = "failed"
        document.ocr_error = "Evidence processing failed. Retry the analysis or continue for manual review."
        document.review_status = "confirmed" if document.reviewed_at else "not_required"
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
    if "invoice" in uploaded:
        uploaded.add("receipt")
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
        for key, value in (document.extracted_data or {}).items() if key in applicable_fields(document.document_type)}
    metadata = document.processing_metadata or {}
    state = {"pending": "UPLOADED", "processing": "PROCESSING", "completed": "COMPLETED",
             "failed": "FAILED", "completed_with_warnings": "NEEDS_REVIEW", "review_required": "NEEDS_REVIEW"}
    result = {"evidence_id": document.document_id, "evidence_category": category(document.document_type),
        "processing_status": state.get(document.ocr_status, "NEEDS_REVIEW"),
        "applicable_fields": list(applicable_fields(document.document_type)),
        "validation_warnings": metadata.get("warnings", []),
        "analysis": {key: metadata[key] for key in ("fields", "quality", "needs_review") if key in metadata},
        "metadata_conflicts": metadata_conflicts(document),
        "id": document.id, "document_id": document.document_id,
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
        "extracted_data": extracted, "verified_data": {key: value for key, value in (document.verified_data or {}).items()
            if key in applicable_fields(document.document_type)},
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
    for field in applicable_fields(document.document_type):
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
    persist_purchase_observations(document)
    return corrected


def metadata_conflicts(document):
    """Observations are review warnings, never automatic coverage decisions."""
    product = document.claim.product if document.claim else None
    if not product:
        return []
    conflicts = []
    for field in applicable_fields(document.document_type):
        item = (document.verified_data or {}).get(field)
        value = item.get("confirmed_value") if item else (document.extracted_data or {}).get(field, {}).get("value")
        name = "name" if field == "product_name" else field
        stored = getattr(product, name, None)
        if field == "warranty_duration" and document.claim.warranty:
            stored = document.claim.warranty.coverage_duration_months
            unit = item.get("unit") if item else (document.extracted_data or {}).get(field, {}).get("unit")
            if value is not None and unit == "years":
                value *= 12
        if stored is None:
            stored = (product.purchase_metadata or {}).get(field, {}).get("confirmed_value")
        if value not in (None, "") and stored not in (None, ""):
            if field == "purchase_price":
                same = Decimal(str(value)) == Decimal(str(stored))
            else:
                same = str(value).strip().casefold() == str(stored).strip().casefold()
            if not same:
                conflicts.append({"field": field, "registered_value": str(stored),
                    "observed_value": value, "source_evidence_id": document.document_id,
                    "requires_review": True})
    return conflicts


def persist_purchase_observations(document):
    if category(document.document_type) not in {"PURCHASE_RECEIPT", "WARRANTY_DOCUMENT", "SERIAL_NUMBER_LABEL"}:
        return
    product = document.claim.product if document.claim else None
    if product is None:
        return
    # Serialize confirmations from different drafts for the same product.
    from backend.db.models import Product
    product = db.session.scalar(select(Product).where(Product.id == product.id).with_for_update()
        .execution_options(populate_existing=True))
    metadata = dict(product.purchase_metadata or {})
    for field, observation in (document.verified_data or {}).items():
        value = observation.get("confirmed_value")
        if value in (None, ""):
            continue
        stored = getattr(product, "name" if field == "product_name" else field, None)
        # Existing registration is authoritative. Never change serial identity,
        # warranty dates, or price from OCR confirmation on a claim attachment.
        if stored not in (None, ""):
            continue
        if field in metadata and metadata[field].get("source_evidence_id") != document.document_id:
            continue
        metadata[field] = {**observation, "source_evidence_id": document.document_id,
            "source_claim_id": document.claim.claim_id, "confirmed_by": document.reviewed_by,
            "confirmed_at": document.reviewed_at.isoformat(), "provenance": "user_confirmed"}
    product.purchase_metadata = metadata
