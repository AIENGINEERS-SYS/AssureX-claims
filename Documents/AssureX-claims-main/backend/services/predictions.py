"""Strict probability contracts and independent inference; no request state is cached."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from threading import Lock

CLASSES = ("valid", "invalid", "manual_review")
LABELS = {"Likely Valid": "valid", "Likely Invalid": "invalid",
          "Manual Review Required": "manual_review", **{c: c for c in CLASSES}}


def probabilities(values, predicted_class=None):
    if not isinstance(values, dict) or set(values) != set(CLASSES):
        raise ValueError("All three class probabilities are required")
    try:
        if any(isinstance(v, bool) or v is None for v in values.values()):
            raise ValueError("Invalid probability")
        scores = {k: Decimal(str(v)) for k, v in values.items()}
        if any(not v.is_finite() or not 0 <= v <= 1 for v in scores.values()):
            raise ValueError("Probabilities must be finite and within [0,1]")
        if abs(sum(scores.values()) - 1) > Decimal("0.001"):
            raise ValueError("Probabilities must sum to one")
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("Invalid probability") from exc
    label = predicted_class if predicted_class is not None else max(CLASSES, key=scores.get)
    if label not in CLASSES or scores[label] != max(scores.values()):
        raise ValueError("Predicted class must have the top confidence")
    # Match database precision so stored evidence and later comparisons reconstruct exactly.
    rounded = {k: float(scores[k].quantize(Decimal("0.000001"))) for k in CLASSES}
    return {"predicted_class": label, "probabilities": rounded, "top_confidence": rounded[label]}


@dataclass(frozen=True)
class ComparisonThresholds:
    strong: Decimal = Decimal("0.10")
    acceptable: Decimal = Decimal("0.20")
    minimum_confidence: Decimal = Decimal("0.70")

    def __post_init__(self):
        for key in ("strong", "acceptable", "minimum_confidence"):
            value = Decimal(str(getattr(self, key)))
            if not value.is_finite() or not 0 <= value <= 1:
                raise ValueError("Comparison thresholds must be finite probabilities")
            object.__setattr__(self, key, value)
        if self.strong >= self.acceptable:
            raise ValueError("Strong threshold must be below acceptable threshold")

    def as_dict(self):
        return {k: float(getattr(self, k)) for k in ("strong", "acceptable", "minimum_confidence")}


def compare_predictions(python, gtm, thresholds=None):
    thresholds = thresholds or ComparisonThresholds()
    result = {"status": "Uncertain Result", "confidence_difference": None,
              "models_agree": None, "thresholds": thresholds.as_dict()}
    if python is None or gtm is None:
        return result
    try:
        left, right = [probabilities(p["probabilities"], p["predicted_class"]) for p in (python, gtm)]
        # Never trust a supplied top_confidence independently of the distribution.
        if any(Decimal(str(p["top_confidence"])) != Decimal(str(q["top_confidence"]))
               for p, q in ((python, left), (gtm, right))):
            return result
    except (ValueError, KeyError, TypeError, InvalidOperation):
        return result
    a, b = Decimal(str(left["top_confidence"])), Decimal(str(right["top_confidence"]))
    difference = abs(a - b)
    agree = left["predicted_class"] == right["predicted_class"]
    result.update(confidence_difference=float(difference), models_agree=agree)
    if min(a, b) < thresholds.minimum_confidence:
        return result
    if not agree:
        result["status"] = "Model Disagreement"
    elif left["predicted_class"] == "manual_review":
        return result
    elif difference <= thresholds.strong:
        result["status"] = "Strong Match"
    elif difference < thresholds.acceptable:
        result["status"] = "Acceptable Match"
    else:
        result["status"] = "Weak Match"
    return result


class PythonBundlePredictor:
    """Load a trusted, operator-supplied notebook bundle once, serialize model calls.

    A bundle is executable pickle data: never accept a path from an HTTP client.
    Missing input features fail explicitly rather than silently impute most of a claim.
    """
    def __init__(self, artifact_path, model_version_id):
        self._path = artifact_path
        self.model_version_id = model_version_id
        self._bundle = None
        self._lock = Lock()

    def predict(self, features):
        with self._lock:
            import joblib
            import pandas as pd
            if self._bundle is None:
                self._bundle = joblib.load(self._path)
            bundle = self._bundle
            columns = bundle["feature_columns"]
            if not columns or len(set(columns)) != len(columns) or not set(columns) <= set(features):
                raise ValueError("Model feature contract is not satisfied")
            pipeline, encoder = bundle["pipeline"], bundle["label_encoder"]
            matrix = pipeline.predict_proba(pd.DataFrame([{k: features[k] for k in columns}], columns=columns))
            classes = encoder.inverse_transform(pipeline.classes_.astype(int))
            labels = [LABELS[c] for c in classes]
            if len(matrix) != 1 or len(labels) != 3 or len(set(labels)) != 3:
                raise ValueError("Invalid model output shape or labels")
            return {"probabilities": dict(zip(labels, matrix[0]))}
