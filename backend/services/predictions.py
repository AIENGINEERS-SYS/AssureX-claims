"""Independent Python and GTM inference adapters with one output contract."""
import hashlib
import json
import time
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from flask import current_app
from sqlalchemy import select
from backend.db.models import ModelVersion, utcnow
from backend.db.services import record_prediction
from backend.extensions import db
from .claim_card import claim_card_path

CANONICAL_LABELS = ("valid", "invalid", "manual_review")
DISPLAY_LABELS = {"valid": "Likely Valid", "invalid": "Likely Invalid", "manual_review": "Manual Review"}


class PredictionError(RuntimeError):
    pass


def canonical_label(value):
    text = str(value or "").strip().casefold().replace("_", " ")
    if "manual" in text or "review" in text:
        return "manual_review"
    if "invalid" in text or "reject" in text:
        return "invalid"
    if "valid" in text or "approve" in text:
        return "valid"
    raise PredictionError("Model returned an unsupported class label.")


def normalized_result(model, version, labels, probabilities, duration_ms):
    scores = {canonical_label(label): float(score) for label, score in zip(labels, probabilities)}
    if set(scores) != set(CANONICAL_LABELS):
        raise PredictionError("Model output does not contain all canonical classes.")
    if any(not 0 <= score <= 1 for score in scores.values()):
        raise PredictionError("Model returned a probability outside the valid range.")
    total = sum(scores.values())
    if abs(total - 1.0) > .01:
        raise PredictionError("Model probabilities do not sum to one.")
    if total != 1:
        scores = {key: value / total for key, value in scores.items()}
    predicted = max(CANONICAL_LABELS, key=lambda key: scores[key])
    return {"prediction": DISPLAY_LABELS[predicted], "prediction_class": predicted,
            "confidence": {key: round(scores[key], 6) for key in CANONICAL_LABELS},
            "top_confidence": round(scores[predicted], 6), "model": model,
            "model_version": version, "inference_duration_ms": duration_ms}


def _artifact_version(path):
    path = Path(path)
    if not path.is_file():
        raise PredictionError("Configured model artifact is unavailable.")
    paths = [path]
    if path.name == "model.json":
        paths.extend((path.with_name("weights.bin"), path.with_name("metadata.json")))
        if any(not item.is_file() for item in paths):
            raise PredictionError("GTM model manifest, weights, or metadata are unavailable.")
    identity = []
    for item in paths:
        stat = item.stat()
        identity.append(f"{item.resolve()}:{stat.st_size}:{stat.st_mtime_ns}")
    return hashlib.sha256("|".join(identity).encode()).hexdigest()[:16]


def _model_version(model_type, name, artifact_path, training_version="unknown"):
    version = _artifact_version(artifact_path)
    item = db.session.scalar(select(ModelVersion).where(ModelVersion.model_type == model_type,
        ModelVersion.model_name == name, ModelVersion.version == version))
    if item is None:
        for previous in db.session.scalars(select(ModelVersion).where(ModelVersion.model_type == model_type,
                ModelVersion.model_name == name, ModelVersion.is_active.is_(True))):
            previous.is_active, previous.retired_at = False, utcnow()
        item = ModelVersion(model_type=model_type, model_name=name, version=version,
            artifact_path=str(Path(artifact_path).resolve()), training_dataset_version=training_version,
            metrics_json={}, is_active=True)
        db.session.add(item)
        db.session.flush()
    return item


@lru_cache(maxsize=4)
def _load_python(path, modified_ns):
    try:
        import joblib
        bundle = joblib.load(path)
    except Exception as exc:
        raise PredictionError("Python model could not be loaded.") from exc
    required = {"pipeline", "label_encoder", "feature_columns"}
    if not isinstance(bundle, dict) or required - bundle.keys() or not hasattr(bundle["pipeline"], "predict_proba"):
        raise PredictionError("Python model bundle has an unsupported contract.")
    return bundle


def _document_value(claim, key):
    for document in claim.documents:
        values = document.verified_data or document.extracted_data or {}
        if isinstance(values, dict) and values.get(key) not in (None, ""):
            return values[key]
    return None


def python_features(claim, policy, columns):
    import numpy as np
    docs = {document.document_type for document in claim.documents}
    claim_date = claim.submission_date or (claim.submitted_at.date() if claim.submitted_at else None)
    purchase = claim.product.purchase_date
    fault = claim.fault_date
    repairs = list(claim.repairs)
    data = {
        "region_zone": "Unknown", "state": "Unknown", "city": "Unknown",
        "customer_type": "Individual", "seller_type": "Unknown", "purchase_channel": "Physical Store",
        "product_category": claim.product.category.title(), "brand": claim.product.brand,
        "model_family": claim.product.model_number, "purchase_price_ngn": float(claim.product.purchase_price),
        "payment_method": "Unknown", "warranty_provider": claim.warranty.provider if claim.warranty else "Unknown",
        "policy_code": policy.code, "warranty_start_basis": "purchase", "warranty_months": policy.warranty_months,
        "days_from_purchase_to_claim": (claim_date - purchase).days if claim_date else np.nan,
        "fault_category": (claim.fault_type or "unknown").strip().casefold().replace(" ", "_"),
        "damage_cause": (claim.damage_type or "uncertain").strip().casefold().replace(" ", "_"),
        "receipt_available": bool({"receipt", "invoice"} & docs),
        "purchase_document_type": "Invoice PDF" if "invoice" in docs else "Printed receipt",
        "warranty_card_available": "warranty_card" in docs,
        "serial_or_imei_available": bool({"serial_number_image", "warranty_card"} & docs),
        "serial_evidence_type": "Product label photo", "product_photo_available": "product_image" in docs,
        "repair_report_available": "repair_report" in docs,
        "fault_evidence_type": "Customer photo" if {"damage_evidence", "fault_evidence"} & docs else "Written fault description only",
        "prior_repairs": len(repairs), "prior_repair_authorized": all(r.authorized_service_center for r in repairs) if repairs else False,
        "return_window_days": 0, "reporting_delay_days": (claim_date - fault).days if claim_date and fault else np.nan,
        "policy_reporting_deadline_days": 30, "requested_remedy": "Repair or replacement",
        "claim_amount_ngn": float(claim.product.purchase_price),
        "document_completeness_score": len(docs & set(policy.required_documents)) / len(policy.required_documents),
        "claim_channel": "Online support form", "manufacture_to_purchase_days": np.nan,
        "purchase_to_fault_days": (fault - purchase).days if fault else np.nan,
        "fault_to_claim_days": (claim_date - fault).days if claim_date and fault else np.nan,
        "claim_month": claim_date.month if claim_date else np.nan,
    }
    return {column: data.get(column, np.nan) for column in columns}


class PythonPredictionService:
    model_type = "python"

    def predict(self, claim, policy):
        path = Path(current_app.config["PYTHON_MODEL_PATH"])
        version = _artifact_version(path)
        bundle = _load_python(str(path.resolve()), path.stat().st_mtime_ns)
        try:
            import pandas as pd
            frame = pd.DataFrame([python_features(claim, policy, bundle["feature_columns"])],
                                 columns=bundle["feature_columns"])
            started = time.perf_counter()
            probabilities = bundle["pipeline"].predict_proba(frame)[0]
            duration = round((time.perf_counter() - started) * 1000)
            labels = bundle["label_encoder"].inverse_transform(range(len(probabilities)))
            result = normalized_result("python", version, labels, probabilities, duration)
            model = _model_version("python", "AssureX XGBoost", path, "assurex-nigeria-v2")
            record = record_prediction(db.session, model_version_id=model.id, claim_id=claim.id,
                predicted_class=result["prediction_class"],
                confidence_valid=Decimal(str(result["confidence"]["valid"])),
                confidence_invalid=Decimal(str(result["confidence"]["invalid"])),
                confidence_manual_review=Decimal(str(result["confidence"]["manual_review"])),
                inference_duration_ms=duration)
            result["prediction_id"] = record.prediction_id
            return result, record
        except PredictionError:
            raise
        except Exception as exc:
            raise PredictionError("Python inference failed.") from exc


@lru_cache(maxsize=2)
def _load_gtm(path, artifact_version):
    try:
        import tensorflowjs as tfjs
        return tfjs.converters.load_keras_model(path)
    except Exception as exc:
        raise PredictionError("GTM runtime or model could not be loaded.") from exc


def _gtm_labels(model_path):
    metadata = Path(model_path).with_name("metadata.json")
    try:
        labels = json.loads(metadata.read_text(encoding="utf-8"))["labels"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise PredictionError("GTM metadata is unavailable or invalid.") from exc
    if not isinstance(labels, list) or len(labels) != 3:
        raise PredictionError("GTM metadata must define three labels.")
    return labels


class GTMPredictionService:
    model_type = "gtm"

    def predict(self, claim, policy):
        model_path = Path(current_app.config["GTM_MODEL_PATH"])
        version = _artifact_version(model_path)
        card = claim_card_path(claim, policy, current_app.config["MODEL_CARD_PATH"])
        started = time.perf_counter()
        predictor = current_app.config.get("GTM_PREDICTOR")
        try:
            if predictor:
                raw = predictor(str(card))
            else:
                import numpy as np
                from PIL import Image
                image = np.asarray(Image.open(card).convert("RGB"), dtype="float32")
                image = ((image / 127.5) - 1.0)[None, ...]
                raw = _load_gtm(str(model_path.resolve()), version).predict(image, verbose=0)[0]
            duration = round((time.perf_counter() - started) * 1000)
            labels = _gtm_labels(model_path)
            if isinstance(raw, dict):
                labels, raw = list(raw), list(raw.values())
            result = normalized_result("gtm", version, labels, raw, duration)
            model = _model_version("gtm", "AssureX GTM Claim Card", model_path, "assurex-gtm-v1")
            record = record_prediction(db.session, model_version_id=model.id, claim_id=claim.id,
                predicted_class=result["prediction_class"],
                confidence_valid=Decimal(str(result["confidence"]["valid"])),
                confidence_invalid=Decimal(str(result["confidence"]["invalid"])),
                confidence_manual_review=Decimal(str(result["confidence"]["manual_review"])),
                claim_summary_card_path=str(card), inference_duration_ms=duration)
            result["prediction_id"] = record.prediction_id
            return result, record
        except PredictionError:
            raise
        except Exception as exc:
            raise PredictionError("GTM inference failed.") from exc
