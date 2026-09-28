# Claim evaluation and audit

The root `backend/`, `frontend/`, and `tests/` directories are the application used by
the root README and commands below. The nested `AssureX-claims/` copy is not an
additional engine and has not been synchronized or deleted.

## Start and migrate

Use the existing Python requirements and frontend build. Run migrations against a
backed-up development database first:

```powershell
.venv\Scripts\python.exe -m flask --app backend:create_app db upgrade
npm.cmd --prefix frontend run build
```

Revision `a614e8b320fd` adds immutable evaluation history and review references. It
does not rewrite legacy recommendations. Its downgrade refuses to delete populated
evaluation history. Tests create disposable SQLite databases; no live database is
modified by the test suite.

## Configure policies explicitly

`WARRANTY_POLICY_PATH` selects a JSON file containing a `policies` array. Each entry
must have exactly these fields (values below are **test examples, not coverage terms**):

```json
{
  "policies": [{
    "policy_code": "TEST-12",
    "version": "test-only-v1",
    "months": 12,
    "required_documents": ["receipt", "serial_number_image"],
    "exclusions": ["Water Damage"],
    "authorized_repair_required": true,
    "serial_case_sensitive": false,
    "final_day_inclusive": true
  }]
}
```

Exclusions match the actual stored fault/damage category exactly. Do not copy
dataset labels into live policies without aligning their vocabulary to claim input
enums. `damage_type` currently records severity in the submission UI; it is not a
verified cause of damage. No free-text inference of exclusions is performed.

Deployment initialization supplies `WARRANTY_POLICY_BINDINGS` as a dictionary of
registered warranty **public IDs** to approved policy codes. Customers cannot set
these bindings. A missing binding yields `POLICY_NOT_FOUND`. The existing descriptive
catalog in `data/assurex_nigeria_policy_rules_v2.json` is retained unchanged. It lacks
the explicit adjudication contract above; an attempt to use its prose entries
returns `POLICY_INVALID_OR_UNAVAILABLE` and routes the claim for review.

The entire policy file is read once per evaluation and its SHA-256 and selected
rules are saved. Changes apply to subsequent evaluations without a stale policy
cache. Registered warranty dates/duration that disagree with the policy require
review; extensions are not silently evaluated using a shorter standard warranty.

## Model providers

Register separate trusted objects in `app.extensions['prediction_providers']` under
`python` and `gtm`. Each must expose `model_version_id` matching the sole active
`ModelVersion` row for its type, and `predict(features)` returning:

```python
{
    "probabilities": {"valid": 0.9, "invalid": 0.06, "manual_review": 0.04},
    # GTM also requires a private, stored card used for this prediction:
    "claim_summary_card_path": "cards/generated-card.png",
}
```

The example above describes a contract; it is not a deployed fallback prediction.
Optional `predicted_class` must name a top-scoring class. Optional `top_confidence`
must agree with the normalized distribution. Probabilities must be finite, within
[0,1], and sum to one within 0.001. Stored scores use six decimal places.

`PythonBundlePredictor(path, model_version_id)` supports the existing trusted joblib
bundle, retains its preprocessing pipeline and label encoder, caches the bundle,
and serializes inference calls. It rejects missing input features. The current
HTTP claim schema does not collect all training features, so **the artifact is not
automatically wired into live adjudication**. Only audited deployment code may
register providers; uploaded files and HTTP requests cannot select executable
model paths. GTM requires its own model/export and card renderer. None is supplied
in this repository; no GTM scores are fabricated.

Providers receive independent copies of a fixed, submission-time feature
allowlist. They receive no final decision, reviewer result, payout, customer ID,
duplicate findings, raw OCR text, or other model's prediction. A failing provider
does not discard its counterpart's successful prediction.

## HTTP workflow

An assigned employee or administrator calls:

```http
POST /api/claims/123/evaluate
Authorization: Bearer <access-token>
Content-Type: application/json

{"version": 4, "idempotency_key": "claim-123-evaluation-4"}
```

The response contains `evaluation` and the updated `claim.version`. The transaction
saves predictions, policy snapshot, rule findings, duplicate evidence, comparison,
decision, explanation and audit log together. Retrying the same key and input
version returns the original evaluation. Reusing a key for different input or
using a stale version returns 409. A new intentional evaluation needs the current
version and a new key; history is retained. Evaluating a finalized claim is blocked.

`GET /api/claims/123/evaluations` is paginated and ownership/assignment scoped.
Customers receive predictions, comparison, recommendation and explanations, without
internal features, hashes, serial provenance or other claim identifiers.
`GET /api/claims/123/reviews` is restricted to reviewers/admins with case access.

Evaluations create advisory recommendations, not payouts or final approvals. All
evaluated claims enter the existing human adjudication queue. Reviewer approval,
rejection, comments and requests for information use the existing actions plus
`POST /api/review/123/request-information`. Review inputs require nonblank `notes`;
the UI also sends `version` for stale-screen conflict detection. ORM version checks
protect overlapping writers even for legacy callers that omit `version`.

Approving/rejecting changes claim status and appends a Review with reviewer identity,
time, original recommendation, reason, override flag and evaluation reference. It
never writes the machine recommendation. Completed reviews leave the pending queue.

## Decision and comparison precedence

1. Failed services, missing/contradictory evidence, unknown policies or duplicate
   indicators require manual review.
2. With sufficient usable evidence, deterministic failures such as expiry or an
   exact configured exclusion produce `likely_invalid`, even if both models say valid.
3. Otherwise both models must agree with sufficient confidence and an acceptable
   confidence gap to recommend `likely_valid` or `likely_invalid`.
4. Remaining cases require manual review.

Defaults are configurable: `MODEL_MINIMUM_CONFIDENCE=0.70`,
`COMPARISON_STRONG_GAP=0.10`, `COMPARISON_ACCEPTABLE_GAP=0.20`.
Strong match includes gap 0.10; acceptable match is above 0.10 and below 0.20;
gap 0.20 is weak. Confidence exactly 0.70 qualifies. Low/missing outputs are
uncertain; confident differing labels are disagreement. Matching manual-review
labels remain uncertain. The stored threshold snapshot reconstructs the comparison.

## Verification

```powershell
.venv\Scripts\python.exe -m pytest -q tests
python -m compileall -q backend tests dataset_generator reports/verify_python_model.py
npm.cmd --prefix frontend run build
python -m dataset_generator.leakage_audit
python reports/verify_python_model.py
```

The last command requires the artifact's compatible pandas, NumPy, scikit-learn,
joblib and XGBoost environment. It performs read-only inference and concurrent-call
checks against the existing held-out rows. It does not retrain or replace the model.
The audit report records actual executions and deployment blockers separately.
