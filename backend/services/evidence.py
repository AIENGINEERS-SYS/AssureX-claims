"""Evidence vocabulary, scoped extraction, and honest visual-analysis boundaries."""
from io import BytesIO
import math
from PIL import Image, ImageStat

CATEGORIES = {
    "receipt": "PURCHASE_RECEIPT", "invoice": "PURCHASE_RECEIPT",
    "product_image": "PRODUCT_IMAGE", "fault_evidence": "DAMAGE_EVIDENCE",
    "damage_evidence": "DAMAGE_EVIDENCE", "serial_number_image": "SERIAL_NUMBER_LABEL",
    "warranty_card": "WARRANTY_DOCUMENT",
}
CATEGORY_TYPES = {"PURCHASE_RECEIPT": "receipt", "PRODUCT_IMAGE": "product_image",
    "DAMAGE_EVIDENCE": "damage_evidence", "SERIAL_NUMBER_LABEL": "serial_number_image",
    "WARRANTY_DOCUMENT": "warranty_card", "OTHER": "other"}
PURCHASE_FIELDS = ("purchase_date", "invoice_number", "product_name", "model_number", "serial_number",
                   "retailer", "purchase_price", "warranty_duration")


def category(kind):
    return CATEGORIES.get(kind, "OTHER")


def applicable_fields(kind):
    return {
        "PURCHASE_RECEIPT": PURCHASE_FIELDS,
        "PRODUCT_IMAGE": ("product_name", "model_number", "serial_number"),
        "SERIAL_NUMBER_LABEL": ("model_number", "serial_number"),
        "WARRANTY_DOCUMENT": ("model_number", "serial_number", "warranty_duration"),
    }.get(category(kind), ())


def required_documents(claim):
    """Registered purchase and identity records replace redundant evidence requests.

    Coverage is still evaluated by the warranty policy engine. This controls
    submission completeness, not whether the claim is covered.
    """
    required = ["product_image", "damage_evidence"]
    product = claim.product
    if not product or not (product.purchase_date and product.retailer.strip() and product.purchase_price is not None):
        required.append("receipt")
    if not product or not (product.serial_number.strip() and product.model_number.strip()):
        required.append("serial_number_image")
    return required


def visual_assessment(content, mime_type, kind):
    """Measure image quality; never treat claim-card classifiers as damage models.

    A deployment can register an actual visual model as evidence_vision in
    Flask.extensions. Its assess(bytes, mime, category) result must contain
    fields, warnings, provider and version. No trained photo model ships here.
    """
    from flask import current_app
    result = {"fields": {}, "warnings": [], "provider": "image-quality", "version": "1",
              "needs_review": True}
    if mime_type.startswith("image/"):
        with Image.open(BytesIO(content)) as picture:
            gray = picture.convert("L")
            gray.thumbnail((512, 512))
            result["quality"] = {"width": picture.width, "height": picture.height,
                "brightness": round(ImageStat.Stat(gray).mean[0], 2)}
            if min(picture.size) < 320:
                result["warnings"].append("Low resolution: upload a clearer photograph if possible.")
            if result["quality"]["brightness"] < 25:
                result["warnings"].append("The photograph is dark; details may be difficult to assess.")
    else:
        result["warnings"].append("A photograph is recommended for visual assessment.")
    provider = current_app.extensions.get("evidence_vision")
    if provider:
        assessment = validate_visual_result(provider.assess(content, mime_type, category(kind)), kind)
        result.update({key: assessment[key] for key in ("fields", "provider", "version", "needs_review")})
        result["warnings"].extend(assessment["warnings"])
    else:
        result["warnings"].append("Visual identification requires manual review; no photographic analysis model is configured."
            if category(kind) == "PRODUCT_IMAGE" else
            "Damage type, affected components and severity require manual assessment; no damage model is configured.")
    return result



def validate_visual_result(assessment, kind):
    from flask import current_app
    from .ocr_service import OCRProcessingError
    allowed = ({"product_name", "product_category", "brand", "model_number", "serial_number"}
        if category(kind) == "PRODUCT_IMAGE" else
        {"damage_category", "description", "affected_components", "severity"})
    def invalid():
        raise OCRProcessingError("The visual provider returned an invalid assessment. Manual review is required.")
    if not isinstance(assessment, dict) or not isinstance(assessment.get("fields", {}), dict):
        invalid()
    fields = assessment.get("fields", {})
    if set(fields) - allowed:
        invalid()
    for field, item in fields.items():
        if not isinstance(item, dict) or set(item) - {"value", "confidence", "state"}:
            invalid()
        confidence = item.get("confidence", 0)
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
            invalid()
        if "state" in item and item["state"] not in {"detected", "uncertain", "not_detected", "ambiguous"}:
            invalid()
        value = item.get("value")
        if value is not None and ((category(kind) == "PRODUCT_IMAGE" and not isinstance(value, str)) or
                (field == "affected_components" and not isinstance(value, list))):
            invalid()
        if value is not None:
            if isinstance(value, str):
                if len(value) > 2000:
                    invalid()
            elif isinstance(value, list):
                if len(value) > 30 or any(not isinstance(x, str) or len(x) > 200 for x in value):
                    invalid()
            elif isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                invalid()
    warnings = assessment.get("warnings", [])
    if not isinstance(warnings, list) or len(warnings) > 30 or any(not isinstance(x, str) or len(x) > 2000 for x in warnings):
        invalid()
    needs_review = assessment.get("needs_review", True)
    if not isinstance(needs_review, bool):
        invalid()
    detected = [item for item in fields.values() if item.get("value") not in (None, "", [])]
    uncertain = not detected or any(item.get("confidence", 0) < current_app.config["OCR_HIGH_CONFIDENCE_THRESHOLD"] for item in detected)
    provider, version = assessment.get("provider"), assessment.get("version")
    if not isinstance(provider, str) or not provider.strip() or len(provider) > 80 or not isinstance(version, str) or not version.strip() or len(version) > 80:
        invalid()
    return {"fields": fields, "warnings": warnings, "provider": provider, "version": version,
            "needs_review": needs_review or uncertain}
