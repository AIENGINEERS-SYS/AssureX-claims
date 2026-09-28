# AssureX implementation and audit report

Date: 2026-09-28. Scope: the root application and supplied checklist, which begins
mid-section and omits several numbered steps. This report distinguishes application
verification from missing deployment inputs. **Production readiness is not established.**

## 1. Executive summary

Audited the Flask/SQLAlchemy APIs, JWT authorization, uploads/OCR, React dashboard,
database migrations, prediction schema/helpers, training notebooks and supplied
XGBoost artifact. The root application had no connected comparison, policy or final
recommendation engine; these now have a canonical implementation with immutable
evaluation snapshots and failure-aware decisions.

Important fixes preserve automated recommendations during human review, enforce
reviewer assignment on the older document route, prevent claim-detail cache
collisions, protect concurrent review actions, and stop previous successful model
outputs from filling gaps in a later failed evaluation.

Missing GTM artifacts, approved machine-readable policy rules/bindings and a full
live-to-training feature mapping prevent completing the requested production
dual-model verification. These conditions now produce explicit review evidence.

## 2. Bugs found and fixed

| Severity | Component / root cause | Fix | Regression coverage |
| --- | --- | --- | --- |
| High | Review approval/rejection replaced `Claim.final_decision` | Separate append-only Review and immutable ClaimEvaluation; preserve machine recommendation | Overrides in both directions, original evidence and reasons retained |
| High | Older document download checked review flag but omitted reviewer assignment | Same assignment scope as current document API | Assigned-away reviewer gets 404 from both routes |
| Medium | Dashboard detail key omitted URL/claim ID | Include URL in React Query key; invalidate queries after actions | Build verified; backend detail/ownership tests; no dedicated dashboard browser test |
| Medium | Two reviewers could finalize on SQLite where row locking is ineffective | SQLAlchemy version checks and HTTP 409; UI passes version | Two simultaneous reviewers produce exactly one action and one conflict |
| Medium | Employee upload increment was unconditional | Conditional version update before storing evidence | Existing upload/auth tests and version conflict checks; no simultaneous employee-upload browser test |
| Medium | Dashboard selected each model's latest success independently | Use prediction references from the latest evaluation | Failed new GTM output does not resurrect an old GTM result |
| Medium | No common finite probability contract | Validate distribution, class maximum and six-decimal persistence; strict JSON provider | NaN, infinity, negatives, missing/empty outputs, incorrect top score |
| Medium | OCR exceptions could include provider data in logs/responses | Generic failure messages and no exception traceback logging | Synthetic sensitive exception absent from output and logs |
| Medium | Training selected every column outside a denylist | Shared explicit 39-feature allowlist used by both notebooks | Outcome fields excluded; exported artifact contract independently checked |
| Medium | Live damage severity could be mistaken for training damage cause | Keep live semantic fields distinct; reject incomplete artifact feature contract | Runtime feature isolation and missing-input behavior |

## 3. Security vulnerabilities

- **Critical:** none established by the executed checks.
- **High:** reviewer assignment bypass on the legacy download route; destruction of
  automated adjudication evidence by human review. Both fixed.
- **Medium:** claim cache collisions, concurrent adjudication integrity, non-finite
  output handling, and provider error disclosure paths. Fixed within the tested scope.
- **Low:** no additional verified finding classified separately.

There is no claim of a comprehensive penetration test or production security certification.

## 4. Leakage audit

| Area | Evidence and limits |
| --- | --- |
| User data / cross-user isolation | Existing owner/RBAC tests plus evaluation and document IDOR regressions. No tenant entity exists, so organization-level tenancy was not tested. |
| Documents | Authenticated private downloads; assignment scope fixed; validation tests cover signatures, MIME, paths, corrupt files and active PDF content. |
| Logging | Database handlers avoid SQL parameters; OCR provider exceptions now suppress messages/tracebacks. Regression injects sensitive text. No review of deployed infrastructure logs. |
| API responses | Customers cannot receive raw model inputs, serial provenance, file hashes or candidate claim identifiers from evaluation history. Reviewer history remains privileged. |
| Cross-request state | Each model receives a fresh feature dictionary. Real Python wrapper locks shared pipeline calls and retains no current-claim field. GTM implementation unavailable. |
| Target leakage | Live inference excludes outcomes/reviews/payouts; notebook feature selection now uses a fixed allowlist. |
| Temporal leakage | Live date contradictions rejected before inference. Dataset split periods overlap; this is not a chronological holdout. Per-feature event provenance is absent. |
| Train/test leakage | All 2,400 dataset rows checked: zero duplicate claim IDs, provenance IDs, or identical retained raw-feature groups crossing splits. Entity-level leakage cannot be ruled out without stable product/customer group IDs. |
| Duplicate leakage | Customers receive only generic review explanations; detailed candidates are stored internally and exposed to authorized staff. |

## 5. Prediction audit

The actual bundle `models/assurex_xgboost_final.joblib` was loaded and evaluated on
all **360 existing test rows**, with **0.925 accuracy**. All returned distributions
passed finite/range/sum checks. Eight concurrent calls matched sequential outputs.
Artifact SHA-256:
`429db3eeacc7137cd571796a55507cfccabb85fdf3833453fb74b644bc7dc144`.

The bundle contains the preprocessing pipeline, label encoder, 39 feature names
and excluded columns. Encoded class order is 0 = Likely Invalid, 1 = Likely Valid,
2 = Manual Review Required. The wrapper uses that encoder and retains the fitted
pipeline instead of recreating preprocessing.

Runtime: NumPy 2.4.3, pandas 3.0.1, scikit-learn 1.9.0, XGBoost 3.2.0. Loading emitted
scikit-learn warnings identifying a 1.8.0 training version and an XGBoost serialization
compatibility warning. These results are an observation, not a compatible-runtime
certification; obtain the original dependency manifest or validate a controlled re-export.

GTM has no implementation/export in the supplied tree. Tests use distinctly
registered **synthetic providers** for application scenarios; those are not evidence
of real GTM accuracy or model independence. Live claims lack several trained features
and use different fault/severity vocabularies. No guessed values are supplied to fill them.

## 6. Model comparison audit

Central configuration defaults: confidence minimum 0.70, strong gap <= 0.10,
acceptable gap > 0.10 and < 0.20, weak gap >= 0.20. Exact boundary tests cover 0.10,
0.20 and 0.70, plus neighboring values. Difference is the absolute difference between
each model's validated predicted-class confidence. Confident differing labels yield
Model Disagreement; missing/low outputs and matching manual-review labels yield
Uncertain Result. Every comparison stores the thresholds used.

## 7. Warranty engine audit

`WARRANTY_POLICY_PATH` and operator-provided warranty-ID bindings select explicit,
versioned JSON rules. No category-specific warranty duration is hard-coded in the
new engine. Full source hash and selected policy are persisted; each new evaluation
rereads the source. Three **test-only policies** (12, 24, 36 months) exercise validation
and reload behavior. Missing definitions, required keys, invalid JSON/YAML content,
missing files and duplicate policy codes fail safely.

The existing descriptive catalog includes, for example, SLOT-DOA-DAP (12 months),
CARL-TECNO-PHONE (13), and SAMSUNG-AFRICA-MOBILE (24), but its prose does not specify
the engine's exact document lists, serial normalization and adjudication contract.
These entries were inspected as local data, not independently validated against
current provider/legal sources, and were not silently converted into approved rules.

The submission wizard's existing baseline document checklist remains a submission
requirement. It is separate from the newly enforced per-policy evaluation checklist.

## 8. Rule engine audit

- Expiry uses calendar month arithmetic with leap-day/month-end clamping and an
  explicit inclusive/exclusive last-day policy. Datetimes normalize to UTC dates.
- Missing/malformed dates, purchase after claim, fault after claim/before purchase,
  repair outside the purchase/claim interval and document purchase-date conflicts
  produce structured review findings.
- Serial comparison preserves claim/product, OCR and confirmed-document provenance.
  It trims surrounding whitespace and optionally changes case; it does not remove
  punctuation. Conflicting OCR evidence is not erased by a user correction.
- Missing required documents, failed/pending OCR and file-integrity failures require review.
- Structured unauthorized repairs and unresolved repair prose require review when
  policy requires authorization. No NLP interpretation of repair declarations is invented.
- Exclusions use configured exact values. Registered warranty terms differing from
  selected rules require review, including ambiguous extensions.

## 9. Duplicate detection audit

Existing upload validation hashes actual file bytes with SHA-256. Evaluation rereads
stored bytes and compares the digest, recording a failure on missing/tampered storage.
Cross-claim matches use the indexed document hash; same registered product is an
additional review indicator. Candidate output is bounded and never implies fraud
merely because the same document or product recurs. Customer responses omit hashes
and other claims' IDs. Existing reviewer duplicate decisions remain separately audited.

## 10. Decision engine audit

Canonical outputs are `likely_valid`, `likely_invalid`, `manual_review_required`,
with the requested human-readable labels. Infrastructure failures, missing policy,
contradictions, missing evidence and duplicate indicators take the review path.
Otherwise deterministic failures take precedence over model agreement. A positive
recommendation requires passed policy checks and two sufficiently confident agreeing
models. No surviving model is treated as dual-model agreement. Recommendations are
advisory; all evaluated claims enter human adjudication, without triggering payouts.

## 11. Explanation audit

Reason, problems and supporting evidence are created by the same pure decision
function consuming stored rules/comparison/predictions. A ClaimEvaluation retains
policy hash/version, inputs, independent model outputs/failures, document hashes,
serial provenance, contradictions, duplicates and engine version. Tests compare
persisted evidence with the HTTP response before and after review. Historical
artifact retention still depends on deployment operations.

## 12. Manual review audit and complete trace

Verified approval, rejection, notes, request-information, resumption, override reasons,
queue removal and append-only review history. Optimistic versioning protects both
PostgreSQL-style locking paths and the executed SQLite tests. Overlapping API calls
from two reviewers produce one finalized Review and a 409 for the other.

The representative HTTP trace in
`test_http_submission_evidence_evaluation_and_review_trace` creates a draft for a
registered product, uploads four real PNGs, submits it, assigns an employee and
evaluates it. Stored file hashes verify. Two synthetic model predictions are saved;
disabled OCR and reused-product evidence cause Manual Review Required. The reviewer
queue contains the claim. Requesting information and resuming review precede approval.
The resulting claim is approved, its machine recommendation remains manual review,
both Review rows reference the evaluation, and audit records preserve the actions.

Legacy recommendations already overwritten before this change cannot be recovered
from missing historical evidence; the migration does not invent them.

## 13. Authentication and RBAC audit

Existing real JWT/Bcrypt tests cover customer, employee, reviewer, administrator,
unauthenticated requests, tampered/expired/revoked tokens and changed database roles.
New tests deny customer/reviewer evaluation writes, unassigned employee evaluation,
cross-owner history, customer access to private review history, and assigned-away
reviewer document downloads. Authorization occurs on the backend independently
of frontend navigation. Privileged input fields remain allowlisted.

## 14. Database changes

Migration `a614e8b320fd`: add `claim_evaluations`, restricted FKs to claim and model
predictions, canonical decision constraint, unique claim/idempotency-key and
claim/input-version constraints, claim index, and nullable indexed Review evaluation
reference with restricted deletion. Existing rows remain readable. Claim ORM version
checking reuses the existing column. Immutable ORM guards include ClaimEvaluation.

Upgrade/downgrade/upgrade was exercised on disposable SQLite databases with existing
reviews and claims and foreign-key validation. Populated evaluation history is
protected by a downgrade refusal. Raw SQL/database-owner access can bypass ORM
immutability; deployment database permissions and backup retention remain necessary.
No production database was reset, migrated or otherwise changed.

## 15. Frontend fixes

Query keys now include the API path. Review actions refresh cached details, queue,
statistics, history and notifications. The review modal shows stored recommendation,
model outputs/failures, comparison and problems, and sends the observed version.
Duplicate-decision requests now send only allowed fields. Existing escaped React
rendering and memory-held token handling remain in place. Both Vite bundles build.

## 16. Tests added

- `test_evaluation_rules.py`: probability/threshold boundaries, calendar dates,
  serials, contradictions, exclusions, repairs, failures and three policy fixtures.
- `test_evaluation_workflow.py`: scenarios A-H, independent failures, JSON safety,
  ownership, RBAC, immutable overrides, retries, transaction rollback, concurrent
  reviewers, stale model aggregation and complete HTTP persistence trace.
- `test_evaluation_migrations.py`: safe schema round trip with retained legacy data.
- `test_training_leakage.py`: explicit training contract and dataset identity/split checks.
- `test_ocr_safety.py`: invalid confidences and provider error privacy.
- Existing dashboard contract and document version tests updated to the stricter behavior.
- Browser claim fixture now creates four distinct image contents. Its former four
  byte-identical images correctly triggered duplicate rejection.

## 17. Executed results

| Executed check | Actual result |
| --- | --- |
| Final complete new regression suite (`test_evaluation_rules`, `test_evaluation_workflow`, `test_evaluation_migrations`, `test_training_leakage`, `test_ocr_safety`) | **67 passed**, 139.07 seconds |
| Existing backend/integration suite, including new OCR safety tests | Initial run: 204 passed, 1 stale-version expectation failed, 3 opt-in browser tests skipped. The stale-version test was corrected to assert 409 and reload; affected authorization/review rerun: **3 passed**, 55 deselected. |
| Dashboard aggregation and review follow-up | **9 passed**, 23 deselected |
| Real Chrome browser suite, final run with `ASSUREX_BROWSER_TESTS=1` | **3 passed**, 86.45 seconds: product/mobile workflow, product XSS, full claim workflow |
| Python source compilation | Passed for backend, tests, dataset audit and artifact verifier |
| Notebook syntax | All **19 code cells** compile; notebooks were not retrained/re-executed |
| Frontend | Both Vite production bundles built successfully; dependency `use client` bundling warnings were nonfatal |
| SQLite migrations | New migration round trip passed with retained claims/reviews; existing migration suites passed in the backend run |
| Real Python model | 360 test rows, 92.5% observed accuracy; finite distributions and matching concurrent outputs; compatibility warnings remain |
| Dataset leakage scan | 2,400 rows checked; no repeated claim/provenance IDs or identical retained raw-feature groups crossing splits; temporal/entity caveats remain |

Collection confirms **270 test cases**. Passing coverage comes from the suite runs
and affected retests above, not a claim that a single final command printed 270 passes.
Initial browser attempts could not launch inside the sandbox, then hit navigation
timeouts. After running outside the sandbox, the claim fixture exposed byte-identical
test uploads; correcting those fixture bytes produced the final three passes.

npm dependency installation reported zero vulnerabilities in its audit. No frontend
lint/type-check script is configured. PostgreSQL, live S3/Redis and real GTM were not
executed. No unexecuted checks are counted as passing.

## 18. Remaining risks and completion status

- **Resolved within tested scope:** immutable review evidence, legacy assignment
  bypass, probability/JSON validation, policy/date/serial contracts, safe missing-model
  handling, retry/transaction guards, review conflicts, dashboard cache isolation and
  model-run pairing, provider error redaction, notebook feature allowlisting.
- **Partially resolved:** runtime model integration, historical reproducibility and
  duplicate assessment. Interfaces and safe failure behavior exist; operational
  artifacts, retention and legitimate-reuse decisions still matter.
- **Blocked on supplied inputs:** GTM export/renderer; approved policy contracts and
  warranty bindings; the complete point-in-time live feature mapping and compatible
  model dependency manifest. These are not replaced by test fixtures.
- **Unverified/deferred:** PostgreSQL multi-worker race behavior, live S3/Redis,
  production OCR installation, chronological/entity-group ML generalization, and
  deployed security/backup permissions. Training data lacks needed provenance.
- **Intentionally retained:** the nested project copy and legacy records. Only root
  callers use the new engine. No retraining, destructive migration or deployment occurred.

The code and executed tests substantially improve the supplied application, but the
checklist's real dual-model, fully configured production completion criteria remain
open for the explicit blockers above. Setup and integration contracts are in
`documentation/evaluation.md`.
