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
feature frame declared by the model bundle. The GTM adapter renders a private 224x224 claim evidence card and
loads the TensorFlowJS layers model lazily.

Production Python deployments should use Python 3.11 or 3.12 and install `backend/requirements.txt`. Model
artifacts are loaded only when their route runs, so ordinary web workers and the other prediction route remain
available if one runtime is unhealthy.

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
