# Automated claim evaluation

AssureX evaluates non-draft claims through independent Python and GTM adapters. A failure in one adapter is
recorded in `evaluation_results.model_errors`; it does not roll back a successful result from the other adapter.

## Routes

- `POST /api/predict/python` with `{ "claim_id": 123 }`
- `POST /api/predict/gtm` with `{ "claim_id": 123 }`
- `POST /api/claims/123/evaluate`
- `GET /api/claims/123/decision`
- `POST /api/review/123/override`
- `GET /api/review/123/audit-history`

Prediction and evaluation routes require an assigned employee, an eligible reviewer, or an administrator.
Customers can retrieve the explanation for their own claim but do not receive model errors, related claim IDs,
or technical model evidence.

## Model contracts

Internal labels are `valid`, `invalid`, and `manual_review`. Both adapters reject out-of-range probabilities,
missing classes, and distributions that do not sum to approximately one. The Python adapter builds the exact
feature frame declared by the model bundle. The GTM adapter uses a private 224x224 claim evidence card.

Production Python deployments should use Python 3.11 or 3.12 and install `backend/requirements.txt`. Phase 30
preloads the Python and GTM artifacts once per serving worker during application startup, then reuses the
in-memory models across requests. Development remains lazy by default so ordinary local commands do not pay
TensorFlow startup cost. Model cache keys include the artifact version, so a replaced model is loaded as a new
cache entry.

## Comparison thresholds

All thresholds are environment-backed Flask configuration:

- Strong Match: both confidences at least `MODEL_STRONG_CONFIDENCE` and gap at most `MODEL_STRONG_MAX_GAP`.
- Acceptable Match: both confidences at least `MODEL_ACCEPTABLE_CONFIDENCE` and gap at most `MODEL_ACCEPTABLE_MAX_GAP`.
- Weak Match: classes agree but the stronger thresholds are not met.
- Model Disagreement: canonical classes differ.
- Uncertain Result: either confidence is below `MODEL_MINIMUM_CONFIDENCE` or a model is unavailable.

## Audit and review

Each combined run appends an immutable evaluation snapshot referencing the exact prediction rows used. Rule
evidence, contradictions, duplicate signals, model comparison, recommendation, and explanation are retained.
Human actions are separate append-only `reviews` rows. A decision that conflicts with the automated
recommendation must use the override route and include an `override_reason`.


## Phase 30 performance behavior

The normal warm prediction path targets approximately **5 seconds or less** for producing both model outputs.

Implemented optimizations include:

- Python and GTM model objects are cached in process memory.
- Production serving workers preload both models at startup by default.
- Railway migration commands explicitly disable preloading so TensorFlow is not loaded just to apply migrations.
- GTM metadata labels are cached by artifact version.
- Deterministic Claim Summary Cards are reused when the rendered evidence digest has not changed.
- Claim loading joins the scalar product/warranty relationships and select-loads document/repair collections, reducing query round trips.
- Warranty policy JSON is parsed and validated once per file version per worker.
- Tesseract executable/version discovery is cached once per worker.
- Scanned PDF pages are converted from PyMuPDF RGB bytes directly instead of PNG-encoding and decoding each page.
- Duplicate-document candidate IDs are de-duplicated in SQL before candidate claim loading.

Prediction endpoints return `total_duration_ms`, `performance_target_ms`, and
`within_performance_target`. Combined evaluation returns a `performance` object with the same target result.
Calls slower than the configured target are logged at warning level.

Configuration:

- `MODEL_PRELOAD_ENABLED`: defaults to `true` in production and `false` elsewhere.
- `MODEL_PRELOAD_STRICT`: defaults to `true` in production. Production startup fails if configured model artifacts cannot be warmed.
- `MODEL_PERFORMANCE_TARGET_MS`: defaults to `5000`.

The automated performance regression test warms both committed model artifacts first and then measures the
dual-model service path, including feature/card preparation, inference, model-version lookup, and prediction
persistence.
