"""Evidence vocabulary, scoped extraction, and honest visual-analysis boundaries."""
from io import BytesIO
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
    if not product or not (product.purchase_date and product.retailer and product.purchase_price is not None):
        required.append("receipt")
    if not product or not (product.serial_number and product.model_number):
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
        assessment = provider.assess(content, mime_type, category(kind))
        # The provider owns confidence/uncertainty; missing certainty means review.
        result.update({key: assessment[key] for key in ("fields", "provider", "version", "needs_review")
                       if key in assessment})
        result["warnings"].extend(assessment.get("warnings", []))
    else:
        result["warnings"].append("Visual identification requires manual review; no photographic analysis model is configured."
            if category(kind) == "PRODUCT_IMAGE" else
            "Damage type, affected components and severity require manual assessment; no damage model is configured.")
    return result
