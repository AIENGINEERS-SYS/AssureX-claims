"""End-to-end verification of the real Python model, real GTM model and comparison engine.

Unlike the ordinary unit tests that may inject a GTM predictor, these tests deliberately
exercise the committed model artifacts. They verify the output contract required by the
AssureX SRS and the final combined evaluation path.
"""
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.db.models import EvaluationResult, GTMPrediction, ModelVersion, PythonPrediction
from backend.extensions import db
from backend.services.predictions import CANONICAL_LABELS
from test_auth import accounts, app, bearer, claims, client, login  # noqa: F401


def auth(client, role):
    return bearer(login(client, role)["access_token"])


def assert_prediction_contract(payload, model_name):
    assert payload["model"] == model_name
    assert payload["prediction_class"] in CANONICAL_LABELS
    assert payload["prediction"] in {"Likely Valid", "Likely Invalid", "Manual Review"}
    assert payload["prediction_id"]
    assert payload["model_version"]

    scores = payload["confidence"]
    assert set(scores) == set(CANONICAL_LABELS)
    assert all(0.0 <= value <= 1.0 for value in scores.values())
    assert sum(scores.values()) == pytest.approx(1.0, abs=0.001)

    expected_top = max(scores.values())
    assert payload["top_confidence"] == pytest.approx(expected_top, abs=1e-6)
    assert scores[payload["prediction_class"]] == pytest.approx(expected_top, abs=1e-6)
    assert payload["inference_duration_ms"] >= 0


def expected_consistency_status(app, python_result, gtm_result):
    py_top = python_result["top_confidence"]
    gtm_top = gtm_result["top_confidence"]
    gap = abs(py_top - gtm_top)

    if py_top < app.config["MODEL_MINIMUM_CONFIDENCE"] or gtm_top < app.config["MODEL_MINIMUM_CONFIDENCE"]:
        return "Uncertain Result"
    if python_result["prediction_class"] != gtm_result["prediction_class"]:
        return "Model Disagreement"
    if (
        py_top >= app.config["MODEL_STRONG_CONFIDENCE"]
        and gtm_top >= app.config["MODEL_STRONG_CONFIDENCE"]
        and gap <= app.config["MODEL_STRONG_MAX_GAP"]
    ):
        return "Strong Match"
    if (
        py_top >= app.config["MODEL_ACCEPTABLE_CONFIDENCE"]
        and gtm_top >= app.config["MODEL_ACCEPTABLE_CONFIDENCE"]
        and gap <= app.config["MODEL_ACCEPTABLE_MAX_GAP"]
    ):
        return "Acceptable Match"
    return "Weak Match"


def test_real_python_and_gtm_models_run_independently(client, app, claims):
    """Both committed artifacts must load and return the required three-class contract."""
    # An injected GTM callable would make this test meaningless. Keep the actual runtime path.
    app.config.pop("GTM_PREDICTOR", None)
    employee = auth(client, "employee")
    claim_id = claims["customer"]["claim"]

    python_response = client.post(
        "/api/predict/python", headers=employee, json={"claim_id": claim_id}
    )
    assert python_response.status_code == 201, python_response.json
    python_result = python_response.json
    assert_prediction_contract(python_result, "python")

    gtm_response = client.post(
        "/api/predict/gtm", headers=employee, json={"claim_id": claim_id}
    )
    assert gtm_response.status_code == 201, gtm_response.json
    gtm_result = gtm_response.json
    assert_prediction_contract(gtm_result, "gtm")

    with app.app_context():
        py_record = db.session.scalar(
            select(PythonPrediction).where(
                PythonPrediction.prediction_id == python_result["prediction_id"]
            )
        )
        gtm_record = db.session.scalar(
            select(GTMPrediction).where(
                GTMPrediction.prediction_id == gtm_result["prediction_id"]
            )
        )
        assert py_record is not None
        assert gtm_record is not None

        py_version = db.session.get(ModelVersion, py_record.model_version_id)
        gtm_version = db.session.get(ModelVersion, gtm_record.model_version_id)
        assert py_version.model_type == "python"
        assert gtm_version.model_type == "gtm"
        assert Path(py_version.artifact_path).is_file()
        assert Path(gtm_version.artifact_path).is_file()

        card = Path(gtm_record.claim_summary_card_path)
        assert card.is_file()
        from PIL import Image

        with Image.open(card) as image:
            assert image.size == (224, 224)
            assert image.mode == "RGB"


def test_real_models_feed_comparison_and_final_evaluation(client, app, claims):
    """The final engine must compare the real outputs and preserve the SRS formula."""
    app.config.pop("GTM_PREDICTOR", None)
    reviewer = auth(client, "reviewer")
    claim_id = claims["other"]["claim"]

    response = client.post(f"/api/claims/{claim_id}/evaluate", headers=reviewer)
    assert response.status_code == 201, response.json

    body = response.json
    evaluation = body["evaluation"]
    python_result = evaluation["python_prediction"]
    gtm_result = evaluation["gtm_prediction"]
    comparison = evaluation["comparison"]

    assert python_result is not None
    assert gtm_result is not None
    assert evaluation["status"] == "complete"
    assert evaluation["model_errors"] == {}

    for result in (python_result, gtm_result):
        assert result["prediction_class"] in CANONICAL_LABELS
        assert set(result["confidence"]) == set(CANONICAL_LABELS)
        assert sum(result["confidence"].values()) == pytest.approx(1.0, abs=0.001)
        assert result["top_confidence"] == pytest.approx(
            max(result["confidence"].values()), abs=1e-6
        )

    # SRS: |Python top-class confidence - GTM top-class confidence|.
    expected_gap = round(
        abs(python_result["top_confidence"] - gtm_result["top_confidence"]), 6
    )
    assert comparison["confidence_difference"] == pytest.approx(expected_gap, abs=1e-6)
    assert comparison["classes_match"] is (
        python_result["prediction_class"] == gtm_result["prediction_class"]
    )
    assert comparison["status"] == expected_consistency_status(
        app, python_result, gtm_result
    )
    assert comparison["status"] in {
        "Strong Match",
        "Acceptable Match",
        "Weak Match",
        "Model Disagreement",
        "Uncertain Result",
    }

    rules = {item["rule_code"]: item for item in body["rules"]}
    assert {
        "warranty_expiry",
        "serial_number_verification",
        "contradiction_detection",
        "missing_documents",
        "repair_authorization",
        "excluded_damage",
    }.issubset(rules)

    # This fixture intentionally has no evidence documents. The SRS requires incomplete
    # evidence to route to human review rather than being auto-approved.
    assert rules["missing_documents"]["result"] == "manual_review"
    assert rules["missing_documents"]["evidence"]["missing_documents"]
    assert evaluation["recommendation"] == "manual_review_required"
    assert evaluation["explanation"]["decision"] == "Manual Review Required"

    with app.app_context():
        stored = db.session.scalar(
            select(EvaluationResult).where(
                EvaluationResult.evaluation_id == evaluation["evaluation_id"]
            )
        )
        assert stored is not None
        assert stored.python_prediction_id is not None
        assert stored.gtm_prediction_id is not None
        assert stored.comparison["confidence_difference"] == pytest.approx(
            expected_gap, abs=1e-6
        )
        assert stored.recommendation == "manual_review_required"


@pytest.mark.parametrize(
    ("python_class", "python_conf", "gtm_class", "gtm_conf", "expected"),
    [
        ("valid", 0.90, "valid", 0.86, "Strong Match"),
        ("invalid", 0.74, "invalid", 0.68, "Acceptable Match"),
        ("manual_review", 0.64, "manual_review", 0.62, "Weak Match"),
        ("valid", 0.86, "invalid", 0.84, "Model Disagreement"),
        ("valid", 0.44, "valid", 0.82, "Uncertain Result"),
    ],
)
def test_comparison_engine_srs_statuses(
    app, python_class, python_conf, gtm_class, gtm_conf, expected
):
    """Challenge all five SRS comparison statuses at their intended policy levels."""
    from backend.services.model_comparison import ModelComparisonService

    def result(predicted, top):
        remainder = (1.0 - top) / 2
        scores = {key: remainder for key in CANONICAL_LABELS}
        scores[predicted] = top
        return {
            "prediction_class": predicted,
            "top_confidence": top,
            "confidence": scores,
        }

    with app.app_context():
        comparison = ModelComparisonService().compare(
            result(python_class, python_conf), result(gtm_class, gtm_conf)
        )

    assert comparison["status"] == expected
    assert comparison["confidence_difference"] == pytest.approx(
        round(abs(python_conf - gtm_conf), 6), abs=1e-6
    )

def test_gtm_accuracy_on_30_shared_unseen_claims(app, tmp_path):
    """Evaluate GTM on 30 claim IDs already designated as held-out Python test records.

    The SRS requires the same underlying claim records to be used for both model
    representations. This test therefore joins the committed Python held-out evidence
    back to the structured dataset, renders the corresponding cards, and evaluates the
    real GTM artifact.
    """
    import csv
    import numpy as np
    from PIL import Image

    from backend.services.model_comparison import ModelComparisonService
    from backend.services.predictions import (
        _artifact_version,
        _gtm_labels,
        _load_gtm,
        normalized_result,
    )
    from dataset_generator.claim_card_generator import (
        LABEL_COLUMNS,
        canonical_label as dataset_label,
        payload,
        render_claim_card,
    )

    root = Path(__file__).resolve().parents[1]
    python_evidence_path = root / "notebooks" / "sample_test_predictions_with_confidence.csv"
    dataset_path = root / "data" / "assurex_nigeria_warranty_claims_v2.csv"
    gtm_path = Path(app.config["GTM_MODEL_PATH"])

    with python_evidence_path.open("r", encoding="utf-8-sig", newline="") as stream:
        python_rows = list(csv.DictReader(stream))[:30]
    assert len(python_rows) == 30

    wanted = {row["claim_id"] for row in python_rows}
    with dataset_path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        label_column = next((name for name in LABEL_COLUMNS if name in reader.fieldnames), None)
        assert label_column is not None
        dataset_rows = {row["claim_id"]: row for row in reader if row.get("claim_id") in wanted}

    assert set(dataset_rows) == wanted

    images = []
    actual = []
    ordered_rows = []
    for index, python_row in enumerate(python_rows):
        row = dataset_rows[python_row["claim_id"]]
        actual_label = dataset_label(row[label_column])
        assert actual_label == dataset_label(python_row["claim_class"])
        card = render_claim_card(payload(row, index), tmp_path / f"{python_row['claim_id']}.png")
        with Image.open(card) as image:
            array = np.asarray(image.convert("RGB"), dtype="float32")
        images.append((array / 127.5) - 1.0)
        actual.append(actual_label)
        ordered_rows.append((python_row, row))

    version = _artifact_version(gtm_path)
    gtm_model = _load_gtm(str(gtm_path.resolve()), version)
    raw_predictions = gtm_model.predict(np.stack(images, axis=0), verbose=0)
    labels = _gtm_labels(gtm_path)
    assert len(raw_predictions) == 30

    gtm_results = [
        normalized_result("gtm", version, labels, probabilities, 0)
        for probabilities in raw_predictions
    ]

    gtm_correct = sum(
        result["prediction_class"] == expected
        for result, expected in zip(gtm_results, actual)
    )
    gtm_accuracy = gtm_correct / len(actual)

    python_correct = sum(
        dataset_label(row["predicted_class"]) == dataset_label(row["claim_class"])
        for row in python_rows
    )
    python_accuracy = python_correct / len(python_rows)

    # The PDF sets an >=85% unseen-test accuracy target for both independent models.
    assert python_accuracy >= 0.85, (
        f"Python 30-claim held-out accuracy {python_accuracy:.3f} is below the SRS 0.85 target"
    )
    assert gtm_accuracy >= 0.85, (
        f"GTM 30-claim held-out accuracy {gtm_accuracy:.3f} "
        f"({gtm_correct}/30) is below the SRS 0.85 target"
    )

    # Also run the comparison engine over the same 30 paired claims.
    with app.app_context():
        for python_row, gtm_result in zip(python_rows, gtm_results):
            py_scores = {
                "valid": float(python_row["confidence_Likely Valid"]),
                "invalid": float(python_row["confidence_Likely Invalid"]),
                "manual_review": float(python_row["confidence_Manual Review Required"]),
            }
            py_class = dataset_label(python_row["predicted_class"])
            py_result = {
                "prediction_class": py_class,
                "confidence": py_scores,
                "top_confidence": max(py_scores.values()),
            }
            comparison = ModelComparisonService().compare(py_result, gtm_result)
            expected_gap = round(
                abs(py_result["top_confidence"] - gtm_result["top_confidence"]), 6
            )
            assert comparison["confidence_difference"] == pytest.approx(expected_gap, abs=1e-6)
            assert comparison["status"] in {
                "Strong Match",
                "Acceptable Match",
                "Weak Match",
                "Model Disagreement",
                "Uncertain Result",
            }
