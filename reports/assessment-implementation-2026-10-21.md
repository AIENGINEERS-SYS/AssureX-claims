# Assessment implementation and verification

Implemented locally on branch `CJ`. The initial working tree was clean. No
production data was migrated, deployment performed, or changes committed/pushed.

## Defects and changes

| Area | Cause found | Implemented behavior |
| --- | --- | --- |
| Submission | `api/claim_workflow.py` saved submissions without a durable assessment/report lifecycle. | Submission and `services/assessments.enqueue` commit together; inference runs in a worker. |
| Evaluation | `services/evaluation.py` populated `Claim.final_decision` from an automated recommendation. | Automated recommendations remain immutable evaluation results; only authorized human review writes a final decision. The migration archives misleading legacy values before clearing them on undecided claims. |
| Report access | Existing aggregate exports did not provide a persistent customer report tied to a claim and evidence version. | `services/assessment_reports.py` creates immutable customer-safe reports; `api/assessments.py` scopes history, view and PDF/CSV/Excel downloads. |
| Recovery | No claim assessment lease, bounded retry or independently recoverable report stage. | Durable jobs, conditional claiming, lease fencing/recovery, bounded backoff, evaluation attempts and separate report retries. |
| Permissions | Implicit administrator inheritance and reviewer access to unassigned/manual-review cases widened access. | `security.role_required` names every permitted role explicitly; `services/access.py` applies owner/assignment predicates to nested resources. Assignment requires an administrator. |
| Shared logic | Upload handlers, route-owned schemas and audit helpers coupled business services to APIs. | Shared upload/audit services and neutral schemas; compatibility imports preserve existing callers. |
| Frontend | Mixed workspaces and overlapping refresh responses could render inappropriate controls or restore an old session. | Central route policy, employee/operations workspaces, denied screens and session revision checks in the shared Axios client. |
| SQLite execution | Cold model loading inside a worker write transaction blocked concurrent requests; a conditional update compared naive/aware timestamps in the ORM identity map. | Cached models warm before the write transaction; claiming uses database predicates without ORM timestamp synchronization. Browser workflow passes. |

## Lifecycle and persistence

Draft -> submitted claim + queued assessment -> worker evaluation -> stored report
and a report-ready notification. Claim status remains authoritative; assessment and
report progress are separate. Uncertainty requires human review. Missing models
produce partial/failed attempts and bounded recovery, never a successful report.

Migration `a46f02c81523` follows `a35e91b70412`. It adds `claims.information_request`,
`assessment_jobs`, `assessment_attempts` and `assessment_reports`, with foreign keys,
version uniqueness and one active job per claim. Evidence/configuration snapshots
and hashes bind each report to its exact evaluation. Identical current inputs reuse
the current assessment; later evidence creates a new version, including returning
to previously assessed input values. Historical reports remain unchanged.

The isolated migration test upgrades, archives former automated decisions, verifies
the schema, and downgrades without restoring misleading decisions. PostgreSQL and
production migrations were not executed locally.

## Route ownership and permissions

Existing compatible URLs remain under their existing authentication, claim,
document, evaluation, review, reporting and administrator blueprints. New
claim-specific assessment routes belong to `assessments`; assigned employee detail
and audit routes belong to `employee`; failed-job administration belongs to
`admin`. The complete registered methods, roles and frontend calls are recorded in
[`documentation/route-inventory.md`](../documentation/route-inventory.md).

| Operation | Customer | Employee | Reviewer | Administrator |
| --- | --- | --- | --- | --- |
| Customer draft creation/edit/submission | Own | Denied | Denied | Denied |
| Read submitted claim/customer-safe report | Own | Assigned | Assigned | Authorized all |
| Upload evidence | Own eligible draft/requested evidence | Assigned, permitted state | Denied | Permitted state |
| Run/retry assessment | Eligible own recovery/requested evidence | Assigned | Assigned | Authorized all |
| Human approve/reject/override | Denied | Denied | Assigned, with required reason/override | With required reason/override |
| Assign/reassign; manage accounts | Denied | Denied | Denied | Allowed |
| Internal evidence/audit | Denied | Relevant assigned claim | Relevant assigned claim | Authorized all |
| Aggregate operational reports | Denied | Restricted assigned scope | Restricted assigned scope | Authorized all |

The reviewer role is the explicit human decision permission; an employee account
does not inherit it. Nested documents, evaluations, reports and duplicate
investigations obey the same scope. Reviewer duplicate investigations require both
claims to be assigned. Customer reports exclude raw errors, storage paths, internal
notes and identifiers of other customers' claims. Downloads recheck identity and
scope after rendering. Existing export writers retain formula-injection protection
and PDF escaping. Audit events record report access, retries, assignments and review.

## Operations and limits

Apply migrations before starting the API, and run `assessment-worker` against the
same database and document storage. Railway's startup script supervises this worker
alongside the existing report export worker. Local SQLite requires one worker.
See [`documentation/assessment-lifecycle.md`](../documentation/assessment-lifecycle.md)
for commands, actual environment settings, state contracts and recovery behavior.

Local Python model integration uses the committed artifact with scikit-learn 1.8.0.
The committed GTM adapter remains unchanged. On this Windows environment,
`tensorflowjs==4.22.0` resolves TensorFlow Decision Forests to a package requiring
TensorFlow 2.15, conflicting with the repository's TensorFlow 2.17 requirement.
The attempted installation failed with `ResolutionImpossible`; no substitute
inference engine or fabricated predictions were introduced. Four real-GTM tests
remain blocked. Deterministic injected GTM predictions cover integrated behavior;
they do not verify the committed GTM runtime or its performance.

Linux production startup, PostgreSQL concurrency and real-GTM performance require
verification in the supported deployment/CI environment. Existing CI retains the
full suite and PostgreSQL migration smoke checks; it was not run remotely here.

## Executed verification

Collection contains 749 tests. Across the backend runs below, **738 distinct tests
passed**, seven opt-in browser tests were skipped, and four real-GTM tests were
explicitly deselected after their runtime blocker was established. This is combined
coverage from split runs, not a claim that one uninterrupted full-suite invocation
passed. Four browser scenarios also passed separately with browser coverage enabled.

| Check | Actual result |
| --- | --- |
| Selected baseline before implementation | 154 passed; no baseline failures in that selection |
| Broad backend fail-fast run | 284 passed, 2 skipped, 4 deselected; stopped on one review fixture missing an explicit assignment |
| Remaining backend files, including that corrected fixture | 500 passed, 5 skipped, 4 deselected; two failures described below |
| Final rerun of the two corrected cases | 2 passed |
| Customer submission/worker/report/mobile/session recovery and role workspaces | 2 browser tests passed |
| Existing aggregate reports and search workspaces | 2 browser tests passed |
| Frontend evidence rendering, mutation queue and session refresh races | All configured checks passed; includes three session race cases |
| Production frontend build | Passed, 2,617 modules transformed |
| Python syntax parsing | All 129 backend/test/inventory Python files parsed successfully |
| Startup shell syntax and `git diff --check` | Passed |

The two final failures were a missing reviewer assignment on an orphan-product
fixture and an old expectation that identical evaluations create duplicate history.
The corrected history test requires identical inputs to reuse the evaluation, then
changed evidence to create a second preserved evaluation. Other fixture updates
explicitly assign authorized reviewers; the review persistence test additionally
asserts 404 before assignment. Security checks were retained.

Warnings are the committed XGBoost artifact's serialization warning, SQLite's
inability to reflect existing expression indexes during migration checks, pypdf's
existing `add_js` deprecation, and dependency `use client` directives ignored by Vite.

These were the broad backend commands (PowerShell):

```powershell
$runtimeExclusions = @(
  '--deselect=tests/test_real_model_pipeline.py::test_real_python_and_gtm_models_run_independently',
  '--deselect=tests/test_real_model_pipeline.py::test_real_models_feed_comparison_and_final_evaluation',
  '--deselect=tests/test_real_model_pipeline.py::test_gtm_accuracy_on_30_shared_unseen_claims',
  '--deselect=tests/test_performance.py::test_warm_real_model_pipeline_meets_five_second_target'
)
.venv\Scripts\python.exe -m pytest -q -x tests @runtimeExclusions --tb=short
.venv\Scripts\python.exe -m pytest -q tests/test_fuzzy_database.py tests/test_fuzzy_models.py tests/test_fuzzy_rules.py tests/test_fuzzy_scenarios.py tests/test_fuzzy_security.py tests/test_migration_heads.py tests/test_notifications.py tests/test_performance.py tests/test_products.py tests/test_product_browser.py tests/test_product_migrations.py tests/test_rate_limiting.py tests/test_real_model_pipeline.py tests/test_reports.py tests/test_report_browser.py tests/test_search.py tests/test_search_browser.py @runtimeExclusions --tb=short
.venv\Scripts\python.exe -m pytest -q tests/test_fuzzy_models.py::test_evaluation_of_a_claim_without_a_product_is_rejected tests/test_fuzzy_models.py::test_repeat_evaluations_reuse_identical_inputs_and_preserve_changed_history --tb=short
```

Frontend/browser commands:

```powershell
npm.cmd --prefix frontend run test:evidence
npm.cmd --prefix frontend run build
$env:ASSUREX_BROWSER_TESTS='1'
.venv\Scripts\python.exe -m pytest -q tests/test_claim_browser.py --tb=short
.venv\Scripts\python.exe -m pytest -q tests/test_report_browser.py tests/test_search_browser.py --tb=short
Remove-Item Env:ASSUREX_BROWSER_TESTS
```

To reproduce all available backend checks in one invocation, use
`.venv\Scripts\python.exe -m pytest -q tests @runtimeExclusions --tb=short`.
In a supported environment with all model dependencies installed, omit the four
exclusions. The exclusions are command-line only; no tests or CI checks were disabled.
