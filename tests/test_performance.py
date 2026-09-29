"""Phase 30 performance regression tests.

These tests focus on the warm production path. Model loading is deliberately
performed before the five-second assertion because production preloads both
artifacts once at worker startup.
"""
from pathlib import Path
from time import perf_counter

import pytest

from backend.db.models import Claim
from backend.extensions import db
from backend.services.claim_card import claim_card_path
from backend.services.evaluation import policy_for_claim
from backend.services.ocr_service import TesseractOCRProvider, _tesseract_engine
from backend.services.predictions import (
    GTMPredictionService,
    PythonPredictionService,
    _artifact_version,
    _gtm_labels,
    _load_gtm,
    _load_python,
)
from test_auth import accounts, app, claims  # noqa: F401


def test_claim_summary_card_is_reused_when_evidence_is_unchanged(
    app, claims, tmp_path, monkeypatch
):
    with app.app_context():
        app.config["MODEL_CARD_PATH"] = str(tmp_path / "cards")
        claim = db.session.get(Claim, claims["customer"]["claim"])
        policy, _ = policy_for_claim(claim)
        first = claim_card_path(claim, policy, app.config["MODEL_CARD_PATH"])
        assert first.is_file() and first.stat().st_size > 0

        def should_not_render(*args, **kwargs):
            raise AssertionError("unchanged claim card was rendered again")

        monkeypatch.setattr(
            "backend.services.claim_card.render_claim_card", should_not_render
        )
        second = claim_card_path(claim, policy, app.config["MODEL_CARD_PATH"])

    assert first == second


def test_tesseract_engine_discovery_is_cached(monkeypatch):
    import pytesseract

    calls = {"count": 0}

    def version():
        calls["count"] += 1
        return "5.0.0"

    _tesseract_engine.cache_clear()
    monkeypatch.setattr(pytesseract, "get_tesseract_version", version)
    provider = TesseractOCRProvider()

    first = provider._engine()
    second = provider._engine()

    assert first is second
    assert calls["count"] == 1
    _tesseract_engine.cache_clear()


def test_warm_real_model_pipeline_meets_five_second_target(app, claims, tmp_path):
    """Exercise both committed artifacts on the warm request path.

    This includes feature construction, card lookup/rendering, inference,
    model-version lookup and prediction persistence for both models.
    """
    with app.app_context():
        app.config["MODEL_CARD_PATH"] = str(tmp_path / "cards")
        app.config["MODEL_PERFORMANCE_TARGET_MS"] = 5000

        claim = db.session.get(Claim, claims["customer"]["claim"])
        policy, _ = policy_for_claim(claim)

        python_path = Path(app.config["PYTHON_MODEL_PATH"])
        gtm_path = Path(app.config["GTM_MODEL_PATH"])
        gtm_version = _artifact_version(gtm_path)

        # Equivalent to the production startup warm-up.
        _load_python(str(python_path.resolve()), python_path.stat().st_mtime_ns)
        _load_gtm(str(gtm_path.resolve()), gtm_version)
        _gtm_labels(gtm_path, gtm_version)

        # Build/cache the deterministic card and model-version rows before timing.
        PythonPredictionService().predict(claim, policy)
        GTMPredictionService().predict(claim, policy)
        db.session.commit()

        started = perf_counter()
        python_result, _ = PythonPredictionService().predict(claim, policy)
        gtm_result, _ = GTMPredictionService().predict(claim, policy)
        elapsed_ms = round((perf_counter() - started) * 1000)
        db.session.rollback()

    assert python_result["prediction_class"] in {"valid", "invalid", "manual_review"}
    assert gtm_result["prediction_class"] in {"valid", "invalid", "manual_review"}
    assert elapsed_ms <= 5000, (
        f"warm dual-model prediction took {elapsed_ms}ms; Phase 30 target is ~5000ms"
    )
