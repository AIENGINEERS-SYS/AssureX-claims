"""Document OCR orchestration, review preservation and completeness policy."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from time import monotonic
from flask import current_app
from sqlalchemy import select, update
from backend.db.models import Document, utcnow
from backend.extensions import db
from .document_extraction import detected_confidence, extract_fields, normalize_text
from .ocr_service import OCRProcessingError, get_ocr_provider
from .evidence import applicable_fields, category, visual_assessment


def extraction(document, field):
    item = (document.extracted_data or {}).get(field)
    if isinstance(item, dict):
        return {"value": None, "confidence": 0.0, "state": "not_detected", **item}
    return {"value": item, "confidence": 0.0, "state": "detected" if item is not None else "not_detected"}


def confirmation(document, field):
    values = document.verified_data or {}
    if field not in values:
        return None
    item = values[field]
    if isinstance(item, dict):
        if "confirmed_value" in item:
            return item
        if "value" in item:
            return {"confirmed_value": item["value"], "ocr_value": item["value"],
                "ocr_confidence": item.get("confidence", 0.0), **({"unit": item["unit"]} if "unit" in item else {})}
        return None
    return {"confirmed_value": item, "ocr_confidence": 0.0}


def effective_review_status(document):
    # Legacy photograph OCR may contain only receipt fields. It must not leave
    # a claim blocked behind an editor that correctly no longer renders them.
    if document.review_status == "pending" and not any(
        extraction(document, field).get("value") is not None
        for field in applicable_fields(document.document_type)):
        return "not_required"
    return document.review_status


def assessment_warnings(document):
    warnings = list((document.processing_metadata or {}).get("warnings", []))
    if category(document.document_type) in {"PRODUCT_IMAGE", "DAMAGE_EVIDENCE"} and not document.processing_metadata:
        warnings.append("This older photograph has no recorded visual assessment and requires manual review.")
    return warnings


def needs_manual_assessment(document):
    if document.ocr_status in {"failed", "review_required", "completed_with_warnings"}:
        return True
    if category(document.document_type) in {"PRODUCT_IMAGE", "DAMAGE_EVIDENCE"}:
        return (document.processing_metadata or {}).get("needs_review", True)
    return False


def processing_document(identifier, public_id, claim_id):
    """Reload a committed record by immutable identity, not a reusable row ID."""
    from werkzeug.exceptions import NotFound
    document = db.session.scalar(select(Document).where(Document.id == identifier,
        Document.document_id == public_id, Document.claim_id == claim_id)
        .execution_options(populate_existing=True))
    if document is None:
        raise NotFound("Evidence was removed while its analysis was running.")
    return document


def start_attempt(document):
    from uuid import uuid4
    document.processing_metadata = {**(document.processing_metadata or {}),
        "attempt_id": uuid4().hex, "attempt_started_at": utcnow().isoformat()}
    document.ocr_status = "processing"
    document.ocr_error = None


def can_retry(document):
    if document.ocr_status in {"failed", "completed_with_warnings"}:
        return True
    if document.ocr_status not in {"pending", "processing"}:
        return False
    started = (document.processing_metadata or {}).get("attempt_started_at")
    try:
        started = datetime.fromisoformat(started) if started else document.created_at
        elapsed = (utcnow().replace(tzinfo=None) - started.replace(tzinfo=None)).total_seconds()
    except (TypeError, ValueError, AttributeError):
        return False
    return elapsed > max(60, current_app.config["OCR_TIMEOUT_SECONDS"] + 30)


def process_document(document: Document, content: bytes, provider=None, *, force=False) -> str:
    """Process synchronously today; this boundary can be called by a future worker unchanged."""
    if document.ocr_processed_at and not force:
        return "ocr_failed" if document.ocr_status == "failed" else "ocr_completed"
    started = monotonic()
    document.ocr_status = "processing"
    document.ocr_error = None
    # Confirmed observations survive retries and provider failures.
    previous_metadata = document.processing_metadata
    attempt_id = (previous_metadata or {}).get("attempt_id")
    previous_text, previous_fields = document.ocr_text, document.extracted_data
    document.processing_metadata = {key: value for key, value in (previous_metadata or {}).items()
        if key in {"attempt_id", "attempt_started_at"}}
    document.processing_metadata["warnings"] = []
    fields = applicable_fields(document.document_type)
    try:
        visual = None
        if category(document.document_type) in {"PRODUCT_IMAGE", "DAMAGE_EVIDENCE"}:
            visual = visual_assessment(content, document.mime_type, document.document_type)
            document.processing_metadata = {**document.processing_metadata, **visual, "warnings": list(visual["warnings"])}
        extracted, raw_text = {}, ""
        result = None
        if fields:
            provider = provider or get_ocr_provider()
            try:
                result = provider.extract_text(content, document.mime_type)
                import math
                if result.confidence is not None and (isinstance(result.confidence, bool) or
                        not isinstance(result.confidence, (int, float)) or not math.isfinite(result.confidence) or
                        not 0 <= result.confidence <= 1):
                    raise OCRProcessingError("The OCR provider returned invalid confidence; manual review is required.")
            except OCRProcessingError:
                # Visible labels are optional on product photographs.
                if category(document.document_type) != "PRODUCT_IMAGE":
                    raise
                result = None
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
        if visual and category(document.document_type) == "PRODUCT_IMAGE":
            for field, item in visual.get("fields", {}).items():
                if field in fields and isinstance(item.get("value"), str) and item["value"].strip():
                    current = extracted.get(field, {})
                    if current.get("value") is None or item.get("confidence", 0) > current.get("confidence", 0):
                        extracted[field] = {**item, "state": "detected"}
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
        document.processing_metadata = previous_metadata
        document.ocr_text, document.extracted_data = previous_text, previous_fields
        document.ocr_status = "failed"
        document.ocr_error = str(exc)[:500]
        document.review_status = "confirmed" if document.reviewed_at else "not_required"
        # Keep the previous successful extraction and all human confirmations.
        if document.extracted_data is None:
            document.extracted_data = {field: {"value": None, "confidence": 0.0, "state": "not_detected"}
                                       for field in fields}
        return "ocr_failed"
    except Exception:
        document.processing_metadata = previous_metadata
        document.ocr_text, document.extracted_data = previous_text, previous_fields
        current_app.logger.exception("Evidence processing failed document_id=%s claim_id=%s", document.id, document.claim_id)
        document.ocr_status = "failed"
        document.ocr_error = "Evidence processing failed. Retry the analysis or continue for manual review."
        document.review_status = "confirmed" if document.reviewed_at else "not_required"
        return "ocr_failed"
    finally:
        document.ocr_processed_at = utcnow()
        document.processing_duration_ms = max(0, round((monotonic() - started) * 1000))
        if attempt_id:
            # A crashed/stalled request can be retried. Its eventual late response
            # must never replace the newer attempt, or resurrect a deleted file.
            changes = {name: getattr(document, name) for name in (
                "ocr_status", "ocr_error", "ocr_text", "extracted_data", "ocr_confidence",
                "ocr_provider", "ocr_provider_version", "ocr_processed_at", "processing_duration_ms",
                "processing_metadata", "review_status")}
            document_id, public_id, claim_id = document.id, document.document_id, document.claim_id
            with db.session.no_autoflush:
                changed = db.session.execute(update(Document).where(Document.id == document_id, Document.document_id == public_id,
                    Document.claim_id == claim_id, Document.processing_metadata["attempt_id"].as_string() == attempt_id).values(**changes),
                    execution_options={"synchronize_session": False})
                db.session.expire(document)
            if changed.rowcount != 1:
                # The caller's response will load the current persisted outcome.
                if db.session.scalar(select(Document.id).where(Document.id == document_id,
                        Document.document_id == public_id, Document.claim_id == claim_id)) is None:
                    from werkzeug.exceptions import NotFound
                    raise NotFound("Evidence was removed while its analysis was running.")
                current_app.logger.info("Discarded superseded evidence attempt document_id=%s", document_id)
                return "ocr_superseded"



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
    extracted = {}
    verified = {}
    for key in applicable_fields(document.document_type):
        if key in (document.extracted_data or {}):
            value = extraction(document, key)
            extracted[key] = {**value, "confidence_label": confidence_label(
                value.get("confidence") if value.get("value") is not None else None)}
        value = confirmation(document, key)
        if value is not None:
            verified[key] = value
    metadata = document.processing_metadata or {}
    state = {"pending": "UPLOADED", "processing": "PROCESSING", "completed": "COMPLETED",
             "failed": "FAILED", "completed_with_warnings": "NEEDS_REVIEW", "review_required": "NEEDS_REVIEW"}
    result = {"evidence_id": document.document_id, "evidence_category": category(document.document_type),
        "processing_status": "NEEDS_REVIEW" if document.ocr_status == "completed" and needs_manual_assessment(document)
            else state.get(document.ocr_status, "NEEDS_REVIEW"),
        "applicable_fields": list(applicable_fields(document.document_type)),
        "can_retry": can_retry(document),
        "validation_warnings": assessment_warnings(document),
        "analysis": {key: metadata[key] for key in ("fields", "quality", "needs_review", "provider", "version") if key in metadata},
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
        "extracted_data": extracted, "verified_data": verified,
        "review_status": effective_review_status(document), "reviewed_at": timestamp(document.reviewed_at),
        "reviewed_by": document.reviewed_by}
    if include_fraud:
        result["cross_claim_duplicate"] = document.cross_claim_duplicate
    if include_text:
        result["ocr_raw_text"] = document.ocr_text or ""
    return result


def review_document(document: Document, corrections: dict, reviewer_id: int) -> bool:
    now = utcnow()
    verified, corrected = {}, False
    for field in applicable_fields(document.document_type):
        observed = extraction(document, field)
        previous = confirmation(document, field)
        original_value = observed.get("value")
        if field == "purchase_price" and isinstance(original_value, dict):
            original_value = original_value.get("amount")
        confirmed = corrections.get(field, previous.get("confirmed_value") if previous is not None else original_value)
        if field == "purchase_date" and hasattr(confirmed, "isoformat"):
            confirmed = confirmed.isoformat()
        if field == "purchase_price" and confirmed is not None:
            value = Decimal(str(confirmed))
            confirmed = int(value) if value == value.to_integral() else float(value)
        if field == "warranty_duration":
            unit = corrections.get("warranty_duration_unit", (previous or {}).get("unit", observed.get("unit"))) if confirmed is not None else None
        else:
            unit = None
        changed = confirmed != original_value or (field == "warranty_duration" and unit != observed.get("unit"))
        corrected = corrected or changed
        item = {"ocr_value": original_value, "confirmed_value": confirmed, "was_corrected": changed,
                "ocr_confidence": observed.get("confidence", 0.0)}
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
    visual_fields = (document.processing_metadata or {}).get("fields", {})
    extra_fields = ("brand", "product_category") if category(document.document_type) == "PRODUCT_IMAGE" else ()
    for field in (*applicable_fields(document.document_type), *extra_fields):
        item = confirmation(document, field)
        value = item.get("confirmed_value") if item is not None else extraction(document, field).get("value")
        if field in extra_fields:
            value = (visual_fields.get(field) or {}).get("value")
        name = {"product_name": "name", "product_category": "category"}.get(field, field)
        stored = getattr(product, name, None)
        if field == "warranty_duration" and document.claim.warranty:
            stored = document.claim.warranty.coverage_duration_months
            unit = item.get("unit") if item is not None else extraction(document, field).get("unit")
            if value is not None and unit == "years":
                value *= 12
        if stored is None:
            saved = (product.purchase_metadata or {}).get(field)
            stored = saved.get("confirmed_value") if isinstance(saved, dict) else saved
        if field == "purchase_price" and isinstance(value, dict):
            value = value.get("amount")
        if value not in (None, "") and stored not in (None, ""):
            if field == "purchase_price":
                same = Decimal(str(value)) == Decimal(str(stored))
            else:
                if field in {"model_number", "serial_number"}:
                    import re
                    same = re.sub(r"[^a-z0-9]", "", str(value).casefold()) == re.sub(r"[^a-z0-9]", "", str(stored).casefold())
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
        if field in metadata and (not isinstance(metadata[field], dict) or
                metadata[field].get("source_evidence_id") != document.document_id):
            continue
        metadata[field] = {**observation, "source_evidence_id": document.document_id,
            "source_claim_id": document.claim.id, "confirmed_by": document.reviewed_by,
            "confirmed_at": document.reviewed_at.isoformat(), "provenance": "user_confirmed"}
    product.purchase_metadata = metadata
