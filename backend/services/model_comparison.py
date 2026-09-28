"""Centralized comparison of normalized model outputs."""
from flask import current_app


class ModelComparisonService:
    def compare(self, python_result, gtm_result):
        if not python_result or not gtm_result:
            return {"python_prediction": python_result["prediction_class"] if python_result else None,
                    "gtm_prediction": gtm_result["prediction_class"] if gtm_result else None,
                    "python_top_confidence": python_result["top_confidence"] if python_result else None,
                    "gtm_top_confidence": gtm_result["top_confidence"] if gtm_result else None,
                    "confidence_difference": None, "distribution_distance": None,
                    "classes_match": None, "status": "Uncertain Result"}
        py_top, gtm_top = python_result["top_confidence"], gtm_result["top_confidence"]
        difference = abs(py_top - gtm_top)
        distance = sum(abs(python_result["confidence"][key] - gtm_result["confidence"][key])
                       for key in ("valid", "invalid", "manual_review")) / 2
        same = python_result["prediction_class"] == gtm_result["prediction_class"]
        minimum = current_app.config["MODEL_MINIMUM_CONFIDENCE"]
        if py_top < minimum or gtm_top < minimum:
            status = "Uncertain Result"
        elif not same:
            status = "Model Disagreement"
        elif (py_top >= current_app.config["MODEL_STRONG_CONFIDENCE"] and
              gtm_top >= current_app.config["MODEL_STRONG_CONFIDENCE"] and
              difference <= current_app.config["MODEL_STRONG_MAX_GAP"]):
            status = "Strong Match"
        elif (py_top >= current_app.config["MODEL_ACCEPTABLE_CONFIDENCE"] and
              gtm_top >= current_app.config["MODEL_ACCEPTABLE_CONFIDENCE"] and
              difference <= current_app.config["MODEL_ACCEPTABLE_MAX_GAP"]):
            status = "Acceptable Match"
        else:
            status = "Weak Match"
        return {"python_prediction": python_result["prediction_class"],
                "gtm_prediction": gtm_result["prediction_class"],
                "python_top_confidence": py_top, "gtm_top_confidence": gtm_top,
                "confidence_difference": round(difference, 6),
                "distribution_distance": round(distance, 6), "classes_match": same, "status": status}
